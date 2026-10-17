"""Authenticated candidate-only helpers. Credentials are read from private files, never printed."""
import json
from pathlib import Path
import ssl
import subprocess
import time

import httpx
import jwt

from scripts.candidate.manage import DEFAULT, PROJECT, verify


def credentials():
    verify(DEFAULT)
    return json.loads((DEFAULT / 'credentials.json').read_text())


def token(customer='candidate-v133-a', lifetime=3600):
    return jwt.encode({'customer_id': customer, 'exp': int(time.time()) + lifetime},
                      credentials()['customer_jwt_secret'], algorithm='HS256')


def http(customer='candidate-v133-a'):
    manifest = verify(DEFAULT)
    return httpx.Client(base_url=manifest['url'] + '/api', timeout=300,
                        verify=ssl.create_default_context(cafile=str(DEFAULT / 'tls/server.crt')),
                        headers={'Authorization': 'Bearer ' + token(customer), 'Origin': manifest['url']})


def api_python(program, data=None):
    manifest = verify(DEFAULT)
    # Assert both database markers before any verification code executes.
    guard = '''
import os,json,sys
from pymongo import MongoClient
import psycopg
candidate_data=json.loads(sys.stdin.read() or '{}')
client=MongoClient(os.environ['MONGO_URI'],serverSelectionTimeoutMS=3000)
db=client[os.environ['MONGO_DATABASE']]
assert db.name=='stockpilot_candidate_v133'
assert db.demo_instance.find_one({'_id':'candidate-v133'})['instance_id']==candidate_data['instance_id']
pg=psycopg.connect(os.environ['POSTGRES_URL'])
assert pg.execute('SELECT current_database()').fetchone()[0]=='stockpilot_candidate_v133'
assert pg.execute('SELECT instance_id FROM demo_instance WHERE id=%s',('candidate-v133',)).fetchone()[0]==candidate_data['instance_id']
'''
    result = subprocess.run(['docker', 'exec', '-i', PROJECT + '-api-1', 'python', '-c', guard + program],
        input=json.dumps({**(data or {}), 'instance_id': manifest['instance_id']}), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Candidate verification command failed; private diagnostics withheld')
    return json.loads(result.stdout)


def snapshot():
    return api_python('''
import hashlib
from bson.json_util import dumps
products=list(db.products.find({}).sort('sku',1))
print(json.dumps({'product_fingerprint':hashlib.sha256(dumps(products).encode()).hexdigest(),
'counts':{name:db[name].count_documents({}) for name in ('products','orders','stock_movements')},
'products':[{k:p[k] for k in ('sku','name','category','price','stock')} for p in products if p['sku'] in ('D-A','D-B','D-C','D-E','D-H','D-O')],
'checkpoints':pg.execute('SELECT count(*) FROM checkpoints').fetchone()[0],
'knowledge_rows':pg.execute('SELECT count(*) FROM knowledge_chunks').fetchone()[0],
'sessions':db.operator_sessions.count_documents({})}))
''')


def write_manual_token():
    path = DEFAULT / 'customer.jwt'
    path.write_text(token(lifetime=86400))
    path.chmod(0o600)
    print(f'Customer JWT written to private file {path}; value withheld.')


if __name__ == '__main__':
    write_manual_token()
