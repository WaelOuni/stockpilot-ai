"""Candidate preflight must refuse unrelated resources before any initialization."""
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.candidate.guards import validate_config, PROJECT

ROOT=Path('/private/candidate')
SOURCE=Path('/private/source')
MANIFEST={'port':5193,'url':'https://localhost:5193'}


def config():
    return {'name':PROJECT,
        'volumes':{key:{'name':PROJECT+'_'+key} for key in ('mongo_data','mongo_config','postgres_data')},
        'networks':{key:{'name':PROJECT+'_'+key,'internal':key in {'front','data'}} for key in ('front','data','egress','public')},
        'services':{
            'mongo':{'volumes':[{'type':'bind','source':str(ROOT/'mongo-key'),'target':'/run/candidate-key','read_only':True}]},
            'postgres':{},'relay':{},'web':{'ports':[{'published':'5193','host_ip':'127.0.0.1'}]},
            'api':{'environment':{
                'MONGO_URI':'mongodb://candidate:synthetic@mongo:27017/?authSource=admin&replicaSet=rs0&directConnection=true',
                'MONGO_DATABASE':'stockpilot_candidate_v133',
                'POSTGRES_URL':'postgresql://candidate:synthetic@postgres:5432/stockpilot_candidate_v133',
                'LANGGRAPH_CHECKPOINT_DB_URI':'postgresql://candidate:synthetic@postgres:5432/stockpilot_candidate_v133',
                'DEPLOYMENT_ENVIRONMENT':'local_demo','ADMIN_SESSION_STORE':'mongo','ADMIN_ALLOW_LOCAL_HTTP':'false',
                'ADMIN_TRUSTED_ORIGIN':MANIFEST['url'],'OLLAMA_BASE_URL':'http://relay:8080',
                'OLLAMA_MODEL':'qwen2.5:7b','OLLAMA_EMBEDDING_MODEL':'nomic-embed-text',
            }},
        }}


def test_valid_candidate_contract_is_read_only():
    original=config()
    before=deepcopy(original)
    validate_config(original,ROOT,SOURCE,MANIFEST)
    assert original==before


@pytest.mark.parametrize('field,value',[
    ('MONGO_URI','mongodb://localhost:27017/stockpilot_demo?replicaSet=rs0'),
    ('MONGO_DATABASE','stockpilot_demo'),
    ('POSTGRES_URL','postgresql://unrelated:synthetic@localhost/stockpilot_demo'),
    ('LANGGRAPH_CHECKPOINT_DB_URI','postgresql://candidate:synthetic@postgres/stockpilot'),
    ('DEPLOYMENT_ENVIRONMENT','development'),('ADMIN_SESSION_STORE','memory'),
    ('ADMIN_ALLOW_LOCAL_HTTP','true'),('ADMIN_TRUSTED_ORIGIN','https://localhost:5191'),
    ('OLLAMA_BASE_URL','http://unrelated:11434'),('OLLAMA_MODEL','different-model'),
])
def test_refuse_changed_database_security_or_model(field,value):
    candidate=config()
    candidate['services']['api']['environment'][field]=value
    with pytest.raises(ValueError): validate_config(candidate,ROOT,SOURCE,MANIFEST)


@pytest.mark.parametrize('case',['stable_port','public_database','external_volume','unrelated_network','foreign_mount','writable_key','wrong_project'])
def test_refuse_shared_or_exposed_candidate_resources(case):
    candidate=config()
    if case=='stable_port': candidate['services']['web']['ports'][0]['published']='5191'
    elif case=='public_database': candidate['services']['mongo']['ports']=[{'published':'27017'}]
    elif case=='external_volume': candidate['volumes']['mongo_data']={'name':'stockpilot-demo-v017_mongo_data','external':True}
    elif case=='unrelated_network': candidate['networks']['data']['name']='stockpilot-demo-v017_data'
    elif case=='foreign_mount': candidate['services']['mongo']['volumes'][0]['source']='/unrelated/private/key'
    elif case=='writable_key': candidate['services']['mongo']['volumes'][0]['read_only']=False
    else: candidate['name']='stockpilot-demo-v017'
    with pytest.raises(ValueError): validate_config(candidate,ROOT,SOURCE,MANIFEST)


@pytest.mark.parametrize('postgres_started,api_started,refresh',[
    ('2026-10-17T17:00:00Z','2026-10-17T16:00:00Z',True),
    ('2026-10-17T16:00:00Z','2026-10-17T17:00:00Z',False),
])
def test_start_reopens_only_api_when_postgres_connection_became_stale(monkeypatch,postgres_started,api_started,refresh):
    from scripts.candidate import manage
    calls=[]
    monkeypatch.setattr('sys.argv',['candidate','start'])
    monkeypatch.setattr(manage,'verify',lambda root:{'revision':'synthetic-reference'})
    monkeypatch.setattr(manage,'compose',lambda root,*args,**kwargs:calls.append(args))
    def inspect(command,**kwargs):
        assert command[:2]==['docker','inspect']
        assert command[2] in (PROJECT+'-postgres-1',PROJECT+'-api-1')
        return postgres_started if command[2].endswith('-postgres-1') else api_started
    monkeypatch.setattr(manage.subprocess,'check_output',inspect)
    manage.main()
    assert calls[0]==('up','-d','--wait')
    assert ('restart','api') in calls if refresh else ('restart','api') not in calls
    assert all(call[:1]!=('restart',) or call==('restart','api') for call in calls)


def test_build_uses_immutable_manifest_revision_instead_of_current_worktree(monkeypatch):
    from scripts.candidate import manage
    revision='b503af9b2fa3c746e8be05eae562a39b66d3ddbe'
    archive=b'synthetic archive of committed source'
    calls=[]
    monkeypatch.setattr('sys.argv',['candidate','build'])
    monkeypatch.setattr(manage,'verify',lambda root:{'revision':revision})
    def git(command,**kwargs):
        assert command==['git','archive','--format=tar',revision]
        return archive
    monkeypatch.setattr(manage.subprocess,'check_output',git)
    monkeypatch.setattr(manage.subprocess,'run',lambda command,**kwargs:calls.append((command,kwargs)))
    manage.main()
    assert len(calls)==2
    for command,kwargs in calls:
        assert command[-1]=='-'
        assert kwargs['input']==archive and kwargs['check']
        assert f'org.opencontainers.image.revision={revision}' in command
        assert any(value.endswith(':'+revision[:12]) for value in command)
