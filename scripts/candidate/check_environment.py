"""Read-only identity proof required before any live candidate/browser validation."""
import json
import subprocess

from scripts.candidate.manage import DEFAULT, PROJECT, verify
from scripts.candidate.runtime import http, snapshot


def main():
    manifest = verify(DEFAULT)
    identifiers = subprocess.check_output(['docker','ps','-aq'], text=True).split()
    containers = json.loads(subprocess.check_output(['docker','inspect',*identifiers],text=True))
    own = [c for c in containers if c['Config'].get('Labels',{}).get('com.docker.compose.project') == PROJECT]
    other = [c for c in containers if c not in own]
    other_mounts = {m['Source'] for c in other for m in c['Mounts']}
    for c in own:
        assert c['State']['Running'], 'Candidate container not running'
        assert not any(m['Source'] in other_mounts for m in c['Mounts']), 'A candidate mount is shared'
        for key, ports in (c['NetworkSettings']['Ports'] or {}).items():
            if ports:
                assert c['Config']['Labels']['com.docker.compose.service']=='web'
                assert ports == [{'HostIp':'127.0.0.1','HostPort':str(manifest['port'])}]
    assert len(own)==5
    current=snapshot()
    with http() as client:
        assert client.get('/health').json()=={'status':'UP'}
    print(json.dumps({'candidate':manifest['url'],'containers':5,'database':manifest['database'],
        'dedicated_mounts_and_networks':True,'products':current['counts']['products'],
        'checkpoints':current['checkpoints']}))


if __name__ == '__main__':
    main()
