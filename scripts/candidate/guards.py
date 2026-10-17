"""Pure candidate configuration checks, independent of Docker and real credentials."""
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict
from pymongo.uri_parser import parse_uri

PROJECT = 'stockpilot-v133-candidate'
DATABASE = 'stockpilot_candidate_v133'


def validate_config(config, root: Path, source: Path, manifest):
    if config.get('name') != PROJECT or set(config['services']) != {'mongo','postgres','api','web','relay'}:
        raise ValueError('Unexpected candidate services/project')
    port = manifest['port']
    if port == 5191 or not 1024 <= port <= 65535:
        raise ValueError('Candidate port must remain distinct')
    for key in ('volumes','networks'):
        if any(value.get('external') or not value.get('name','').startswith(PROJECT+'_') for value in config[key].values()):
            raise ValueError('External or unrelated candidate resource')
    if set(config['volumes']) != {'mongo_data','mongo_config','postgres_data'}:
        raise ValueError('Unexpected candidate volumes')
    if not all(config['networks'][key]['internal'] for key in ('front','data')):
        raise ValueError('Candidate private networks must remain internal')
    approved = {
        'mongo': {'/run/candidate-key':str(root/'mongo-key')},
        'relay': {'/candidate/relay.py':str(source/'scripts/candidate/relay.py'), '/candidate/control':str(root/'control')},
        'web': {'/etc/nginx/conf.d/default.conf':str(root/'web.conf'), '/candidate/server.crt':str(root/'tls/server.crt'), '/candidate/server.key':str(root/'tls/server.key')},
        'api': {}, 'postgres': {},
    }
    for service, values in config['services'].items():
        for mount in values.get('volumes',[]):
            if mount['type']=='bind' and (approved[service].get(mount['target'])!=mount['source'] or not mount.get('read_only')):
                raise ValueError('Unexpected candidate bind mount')
            if mount['type']=='volume' and mount['source'] not in config['volumes']:
                raise ValueError('Unexpected candidate volume mount')
        ports=values.get('ports',[])
        if service!='web' and ports:
            raise ValueError('Only candidate TLS frontend may publish a port')
        if service=='web' and (len(ports)!=1 or str(ports[0]['published'])!=str(port) or ports[0].get('host_ip')!='127.0.0.1'):
            raise ValueError('Candidate browser must bind loopback only')
    api=config['services']['api']['environment']
    parsed=parse_uri(api['MONGO_URI'])
    postgres=conninfo_to_dict(api['POSTGRES_URL'])
    checkpoint=conninfo_to_dict(api['LANGGRAPH_CHECKPOINT_DB_URI'])
    if (api['MONGO_DATABASE']!=DATABASE or parsed['nodelist']!=[('mongo',27017)]
            or parsed.get('database') not in (None,DATABASE) or parsed['options'].get('replicaSet')!='rs0'):
        raise ValueError('Unexpected candidate Mongo target')
    if any(value.get('host')!='postgres' or value.get('dbname')!=DATABASE for value in (postgres,checkpoint)):
        raise ValueError('Unexpected candidate PostgreSQL target')
    if (api['DEPLOYMENT_ENVIRONMENT']!='local_demo' or api['ADMIN_SESSION_STORE']!='mongo'
            or api['ADMIN_ALLOW_LOCAL_HTTP']!='false' or api['ADMIN_TRUSTED_ORIGIN']!=manifest['url']
            or api['OLLAMA_BASE_URL']!='http://relay:8080' or api['OLLAMA_MODEL']!='qwen2.5:7b'
            or api['OLLAMA_EMBEDDING_MODEL']!='nomic-embed-text'):
        raise ValueError('Candidate security/model configuration changed')
