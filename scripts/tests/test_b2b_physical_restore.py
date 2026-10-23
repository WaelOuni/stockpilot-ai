import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import b2b_physical_restore as p


def tar_bytes(entries):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w',format=tarfile.PAX_FORMAT) as t:
        for name,kind,extra in entries:
            m=tarfile.TarInfo(name);m.type=kind;m.uid=999;m.gid=70;m.mode=0o700;m.mtime=1700000000
            for k,v in extra.items():setattr(m,k,v)
            content=b'test' if kind==tarfile.REGTYPE else None
            m.size=len(content) if content else 0
            t.addfile(m,io.BytesIO(content) if content else None)
    return out.getvalue()


ROOT=[('.',tarfile.DIRTYPE,{})]

class ArchiveTests(unittest.TestCase):
    def test_mixed_numeric_owners_override_header_names_in_inventory(self):
        raw=tar_bytes(ROOT+[('nested',tarfile.DIRTYPE,{'uid':70,'gid':70,'mode':0o750}),
            ('nested/file',tarfile.REGTYPE,{'uid':12345,'gid':23456,'uname':'root','gname':'root','mode':0o400})])
        inv=p.inventory(io.BytesIO(raw))
        self.assertEqual((inv['nested/file']['uid'],inv['nested/file']['gid']),(12345,23456))
        self.assertEqual(inv['.']['mode'],0o700)
        self.assertEqual(inv['nested']['mode'],0o750)

    def test_unsafe_paths_and_normalized_duplicates(self):
        for name in ['/absolute','../escape','x/../escape','line\nbreak']:
            with self.subTest(name=name),self.assertRaises(p.RestoreRefused):
                p.inventory(io.BytesIO(tar_bytes(ROOT+[(name,tarfile.REGTYPE,{})])))
        with self.assertRaises(p.RestoreRefused):
            p.inventory(io.BytesIO(tar_bytes(ROOT+[('file',tarfile.REGTYPE,{}),('./file',tarfile.REGTYPE,{})])))

    def test_links_devices_and_fifos_refused(self):
        for kind in [tarfile.SYMTYPE,tarfile.LNKTYPE,tarfile.CHRTYPE,tarfile.BLKTYPE,tarfile.FIFOTYPE]:
            with self.subTest(kind=kind),self.assertRaises(p.RestoreRefused):
                p.inventory(io.BytesIO(tar_bytes(ROOT+[('bad',kind,{'linkname':'elsewhere'})])))

    def test_root_and_explicit_directory_ancestors_required(self):
        for entries in [[('file',tarfile.REGTYPE,{})],ROOT+[('missing/file',tarfile.REGTYPE,{})],
            ROOT+[('file',tarfile.REGTYPE,{}),('file/child',tarfile.REGTYPE,{})]]:
            with self.subTest(entries=entries),self.assertRaises(p.RestoreRefused):
                p.inventory(io.BytesIO(tar_bytes(entries)))

    def test_unqualified_xattrs_and_numeric_metadata_refused(self):
        for extra in [{'pax_headers':{'SCHILY.xattr.security.capability':'value'}},
                      {'pax_headers':{'LIBARCHIVE.xattr.user.test':'value'}},
                      {'pax_headers':{'unknown.metadata':'value'}},{'uid':-1},{'gid':-1},{'mtime':-1}]:
            with self.subTest(extra=extra),self.assertRaises(p.RestoreRefused):
                p.inventory(io.BytesIO(tar_bytes(ROOT+[('file',tarfile.REGTYPE,extra)])))
        # TarInfo.tobuf masks oversized modes, so encode this malformed header directly.
        raw=bytearray(tar_bytes(ROOT+[('file',tarfile.REGTYPE,{})]))
        raw[612:620]=b'0010000\0'
        raw[660:668]=b'        '
        raw[660:668]=('%06o\0 ' % sum(raw[512:1024])).encode()
        with self.assertRaisesRegex(p.RestoreRefused,'invalid_numeric_metadata'):
            p.inventory(io.BytesIO(raw))

    def test_member_and_payload_limits_and_truncation(self):
        raw=tar_bytes(ROOT+[('file',tarfile.REGTYPE,{})])
        with self.assertRaises(p.RestoreRefused):p.inventory(io.BytesIO(raw),max_members=1)
        with self.assertRaises(p.RestoreRefused):p.inventory(io.BytesIO(raw),max_bytes=3)
        with self.assertRaises((p.RestoreRefused,tarfile.TarError)):p.inventory(io.BytesIO(raw[:1200]))

    def test_digest_mismatch_and_archive_symlink_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'archive.tar';path.write_bytes(tar_bytes(ROOT))
            with self.assertRaisesRegex(p.RestoreRefused,'digest_mismatch'):p.checked_archive(path,'a'*64)
            alias=Path(directory)/'alias';alias.symlink_to(path)
            with self.assertRaisesRegex(p.RestoreRefused,'regular_archive'):p.checked_archive(alias,hashlib.sha256(path.read_bytes()).hexdigest())


class GuardTests(unittest.TestCase):
    def session(self):
        s=p.RestoreSession.__new__(p.RestoreSession);s.prefix='stockpilot-v1394-r1-'+'a'*32;s.image='sha256:'+'b'*64
        s.preexisting={'source':{'Mountpoint':'/var/lib/docker/volumes/source/_data'}}
        self.volume=s.prefix+'-data';self.cid='c'*64
        v={'Name':self.volume,'Driver':'local','Scope':'local','Options':None,
           'Mountpoint':'/var/lib/docker/volumes/'+self.volume+'/_data',
           'Labels':{'stockpilot.restore.campaign':s.prefix,'stockpilot.audit.purpose':'v1394-r1-physical-restore'}}
        s.volumes={self.volume:copy.deepcopy(v)};s.helpers={self.cid:{'name':s.prefix+'-extract-data','volume':self.volume,'user':'0:0','arguments':p.EXTRACT_ARGUMENTS}}
        s.record=Mock();s.event=Mock();s.data=Mock(return_value=[v])
        return s,v

    def helper_row(self,s):
        return {'Id':self.cid,'Name':'/'+s.helpers[self.cid]['name'],'Image':s.image,
          'Config':{'Entrypoint':['/bin/tar'],'Cmd':p.EXTRACT_ARGUMENTS,'User':'0:0','Env':['TAR_OPTIONS='],'Healthcheck':{'Test':['NONE']},'Labels':{'stockpilot.restore.campaign':s.prefix}},
          'HostConfig':{'NetworkMode':'none','PortBindings':{},'ReadonlyRootfs':True,'RestartPolicy':{'Name':'no'},
            'Privileged':False,'PidMode':'','IpcMode':'private','UTSMode':'','CapDrop':['ALL'],
            'CapAdd':['CAP_CHOWN','CAP_DAC_OVERRIDE','CAP_FOWNER','CAP_FSETID'],'Binds':None,'VolumesFrom':None},
          'Mounts':[{'Type':'volume','Name':self.volume,'Destination':'/restore','RW':True}],
          'State':{'Status':'created','Running':False,'Pid':0}}

    def test_volume_alias_driver_options_labels_and_changed_identity_refused(self):
        for field,value in [('Driver','nfs'),('Options',{'device':'source'}),('Mountpoint','/var/lib/docker/volumes/source/_data'),('Labels',{})]:
            s,v=self.session();v[field]=value
            with self.subTest(field=field),self.assertRaises(p.RestoreRefused):s.attest_volume(self.volume)
        s,v=self.session();v['CreatedAt']='replaced'
        with self.assertRaisesRegex(p.RestoreRefused,'changed_since'):s.attest_volume(self.volume)

    def test_existing_or_foreign_volume_refused_before_inspection(self):
        s,v=self.session()
        with self.assertRaises(p.RestoreRefused):s.attest_volume('source')
        s.data.assert_not_called()
        with patch.object(p,'execute',return_value=(self.volume+'\n').encode()) as run:
            with self.assertRaises(p.RestoreRefused):s.new_volume('data')
            self.assertEqual(run.call_count,1)

    def test_duplicate_new_mountpoints_refused(self):
        s,v=self.session();s.volumes['other']=dict(v)
        with self.assertRaisesRegex(p.RestoreRefused,'shared_or_unexpected'):s.attest_volume(self.volume)

    def test_helper_capability_spelling_is_equivalent_but_extra_privilege_refused(self):
        for prefix in ('','CAP_'):
            s,v=self.session();row=self.helper_row(s);row['HostConfig']['CapAdd']=[prefix+x for x in ['CHOWN','DAC_OVERRIDE','FOWNER','FSETID']]
            s.data=Mock(return_value=[row]);s.attest_volume=Mock()
            with patch.object(p,'execute',return_value=(self.cid+'\n').encode()):s.attest_helper(self.cid,stopped=True)
        row['HostConfig']['CapAdd'].append('CAP_SYS_ADMIN')
        with self.assertRaises(p.RestoreRefused):s.attest_helper(self.cid,stopped=True)

    def test_network_bind_engine_entrypoint_or_extra_consumer_refused(self):
        for mutation in ('network','bind','entrypoint','consumer','rootfs','pid','tar_options'):
            s,v=self.session();row=self.helper_row(s);s.attest_volume=Mock()
            if mutation=='network':row['HostConfig']['NetworkMode']='host'
            if mutation=='bind':row['Mounts'].append({'Type':'bind','Source':'/source'})
            if mutation=='entrypoint':row['Config']['Entrypoint']=['mongod']
            if mutation=='rootfs':row['HostConfig']['ReadonlyRootfs']=False
            if mutation=='pid':row['HostConfig']['PidMode']='host'
            if mutation=='tar_options':row['Config']['Env']=['TAR_OPTIONS=--to-command=bad']
            s.data=Mock(return_value=[row])
            users=(self.cid+'\n'+('d'*64+'\n' if mutation=='consumer' else '')).encode()
            with patch.object(p,'execute',return_value=users),self.assertRaises(p.RestoreRefused):s.attest_helper(self.cid,stopped=True)

    def test_foreign_container_cannot_be_started(self):
        s,v=self.session()
        with patch.object(p,'execute') as run,self.assertRaises(p.RestoreRefused):s.start_helper('e'*64)
        run.assert_not_called()

    def test_invalid_archive_or_destination_prevents_import(self):
        s,v=self.session();s.new_volume=Mock(side_effect=p.RestoreRefused('unsafe_volume'));s.new_helper=Mock();s.start_helper=Mock()
        with patch.object(p,'checked_archive',side_effect=p.RestoreRefused('bad_archive')):
            with self.assertRaises(p.RestoreRefused):s.restore('data','unused','a'*64)
        s.new_volume.assert_not_called()
        with patch.object(p,'checked_archive',return_value={'.':{}}):
            with self.assertRaises(p.RestoreRefused):s.restore('data','unused','a'*64)
        s.new_helper.assert_not_called();s.start_helper.assert_not_called()

    def test_nonempty_destination_prevents_import(self):
        s,v=self.session();s.new_volume=Mock(return_value=self.volume);s.new_helper=Mock(return_value=self.cid)
        s.readback=Mock(return_value={'.':{},'existing':{}});s.start_helper=Mock()
        with patch.object(p,'checked_archive',return_value={'.':{}}),self.assertRaisesRegex(p.RestoreRefused,'not_empty'):
            s.restore('data','unused','a'*64)
        s.start_helper.assert_not_called()

    def test_archive_changed_after_validation_refuses_before_start(self):
        s,v=self.session();s.new_volume=Mock(return_value=self.volume);s.new_helper=Mock(return_value=self.cid)
        s.readback=Mock(return_value={'.':{}});s.start_helper=Mock()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'input.tar';raw=tar_bytes(ROOT);path.write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
            expected=p.checked_archive(path,sha)
            def validated_then_changed(*args):path.write_bytes(b'changed');return expected
            with patch.object(p,'checked_archive',side_effect=validated_then_changed),self.assertRaisesRegex(p.RestoreRefused,'changed_before_import'):
                s.restore('data',path,sha)
        s.start_helper.assert_not_called()

    def test_root_owner_mismatch_after_extraction_is_not_accepted(self):
        s,v=self.session();s.new_volume=Mock(return_value=self.volume);s.new_helper=Mock(return_value=self.cid);s.start_helper=Mock()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'input.tar';raw=tar_bytes(ROOT);path.write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
            bad=p.checked_archive(path,sha);bad['.']['uid']=0
            s.readback=Mock(side_effect=[{'.':{}},bad])
            with self.assertRaisesRegex(p.RestoreRefused,'restored_file_or_metadata_mismatch'):s.restore('data',path,sha)

    def test_nonzero_stderr_timeout_never_succeed(self):
        for result in [subprocess.CompletedProcess([],1,b'',b''),subprocess.CompletedProcess([],0,b'',b'warning')]:
            with patch.object(p.subprocess,'run',return_value=result),self.assertRaises(p.RestoreRefused):p.execute(['not-executed'])
        with patch.object(p.subprocess,'run',side_effect=subprocess.TimeoutExpired('test',1)),self.assertRaises(p.RestoreRefused):p.execute(['not-executed'])


if __name__=='__main__':unittest.main()
