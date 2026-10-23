import copy
import unittest
from scripts import b2b_cold_preflight as p

class PreflightRefusals(unittest.TestCase):
    def setUp(self):
        self.services=sorted(p.SERVICES)
        self.ids={s:format(i+1,'064x') for i,s in enumerate(self.services)}
        self.manifest={'containers':self.ids,'images':{s:'sha256:'+format(i+100,'064x') for i,s in enumerate(self.services)},
                       'volumes':{name:{} for mounts in p.VOLUMES.values() for name in mounts}}
        self.rows=[{'Id':cid,'Image':self.manifest['images'][s],
                    'State':{'Status':'exited','Running':False,'Pid':0,'ExitCode':255},
                    'Mounts':[{'Type':'volume','Name':name,'Destination':target} for name,target in p.VOLUMES.get(s,{}).items()]}
                   for s,cid in self.ids.items()]
        self.live='f'*64
        self.rows.append({'Id':self.live,'State':{'Running':True},'Mounts':[]})
        self.windows={'version':1,'result':'PASS','free_bytes':40*1024**3,
           'processes':{'positive_control':True,'process_count':2,'candidates_checked':1,'concurrent_admin':[]},
           'backing_files':[{'path':r'C:\wsl\ext4.vhdx','drive':'C','length':1},{'path':r'C:\Docker\docker_data.vhdx','drive':'C','length':1}]}

    def refuse(self, fn, code):
        with self.assertRaisesRegex(p.Refused,code):fn()

    def test_valid_sources(self):
        sources,live=p.validate_sources(self.rows,self.manifest)
        self.assertEqual(live,{self.live});self.assertEqual(len(sources),6)

    def test_container_identity(self):
        self.rows[0]['Id']='e'*64
        self.refuse(lambda:p.validate_sources(self.rows,self.manifest),'source_container_identity')

    def test_image_identity(self):
        self.rows[0]['Image']='foreign'
        self.refuse(lambda:p.validate_sources(self.rows,self.manifest),'source_image_identity')

    def test_running_or_pid(self):
        for field,value in [('Running',True),('Pid',42),('ExitCode',0)]:
            rows=copy.deepcopy(self.rows);rows[0]['State'][field]=value
            self.refuse(lambda:p.validate_sources(rows,self.manifest),'source_not_stopped')

    def test_volume_shared(self):
        self.rows[-1]['Mounts']=[{'Type':'volume','Name':'stockpilot-b2b-local_mongo-data'}]
        self.refuse(lambda:p.validate_sources(self.rows,self.manifest),'volume_has_other_consumer')

    def test_volume_destination(self):
        row=next(r for r in self.rows if r.get('Mounts'));row['Mounts'][0]['Destination']='/wrong'
        self.refuse(lambda:p.validate_sources(self.rows,self.manifest),'volume_destination_identity')

    def test_no_live_control(self):
        self.rows.pop()
        self.refuse(lambda:p.validate_sources(self.rows,self.manifest),'positive_control_or_ids_invalid')

    def test_cgroups(self):
        p.validate_cgroups(self.live,set(self.ids.values()),{self.live})
        self.refuse(lambda:p.validate_cgroups('',set(self.ids.values()),{self.live}),'positive_cgroup_control_missing')
        self.refuse(lambda:p.validate_cgroups(self.live+' '+next(iter(self.ids.values())),set(self.ids.values()),{self.live}),'source_process_present')

    def test_wsl_distributions(self):
        self.assertEqual(len(p.active_distros('Ubuntu\ndocker-desktop'.encode('utf-16-le'))),2)
        self.refuse(lambda:p.active_distros(b'Ubuntu\ndocker-desktop\nOther'),'unobserved_running_wsl_distribution')

    def test_windows_positive(self):
        p.validate_windows(self.windows,36*1024**3)

    def test_windows_unknown_or_insufficient(self):
        for value in (None,False,'9999999999999',1):
            doc=copy.deepcopy(self.windows);doc['free_bytes']=value
            self.refuse(lambda:p.validate_windows(doc,36*1024**3),'physical_reserve')

    def test_windows_unreadable_process_inventory(self):
        for field,value in [('positive_control',False),('candidates_checked',0),('concurrent_admin',[12])]:
            doc=copy.deepcopy(self.windows);doc['processes'][field]=value
            self.refuse(lambda:p.validate_windows(doc,36*1024**3),'windows_process_evidence_incomplete')

    def test_windows_backing_files(self):
        for field,value in [('drive','D'),('length',None),('path',r'D:\wrong.vhdx')]:
            doc=copy.deepcopy(self.windows);doc['backing_files'][0][field]=value
            self.refuse(lambda:p.validate_windows(doc,36*1024**3),'backing_file_unproven')
        doc=copy.deepcopy(self.windows);doc['backing_files'][1]=doc['backing_files'][0]
        self.refuse(lambda:p.validate_windows(doc,36*1024**3),'backing_identity_unproven')

if __name__=='__main__':unittest.main()
