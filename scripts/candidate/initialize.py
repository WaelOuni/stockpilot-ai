"""Insert-only initialization of the verified, newly created V13.3 candidate stores."""
import json
from pathlib import Path
import subprocess
import time

from scripts.candidate.manage import PROJECT

from scripts.candidate.manage import DEFAULT, ROOT, compose, verify


def main():
    manifest = verify(DEFAULT)
    auth = 'db.getSiblingDB("admin").auth(process.env.MONGO_INITDB_ROOT_USERNAME,process.env.MONGO_INITDB_ROOT_PASSWORD);'
    command = auth + 'try{rs.status()}catch(e){rs.initiate({_id:"rs0",members:[{_id:0,host:"mongo:27017"}]})}'
    compose(DEFAULT, 'exec', '-T', 'mongo', 'mongosh', '--quiet', '--eval', command,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        result = compose(DEFAULT, 'exec', '-T', 'mongo', 'mongosh', '--quiet', '--eval',
            auth + 'print(db.hello().isWritablePrimary)', capture_output=True, text=True)
        if result.stdout.strip().endswith('true'):
            break
        time.sleep(1)
    else:
        raise RuntimeError('Candidate primary not ready')
    # Reuse the existing generator; only curated Advice examples extend it.
    from evals.fixtures.catalogue_advice import catalogue
    examples = catalogue()
    products = [{'sku': p.sku, 'name': p.name, 'category': p.category.value,
                 'price': {'D-A': 29.9, 'D-B': 39.9, 'D-C': 49.9}.get(p.sku, p.price), 'stock': p.stock} for p in examples]
    products += [{'sku': 'C-SAND-1', 'name': 'Ponceuse Synthétique Compacte', 'category': 'SANDER', 'price': 59.9, 'stock': 4},
                 {'sku': 'C-SAND-2', 'name': 'Ponceuse Synthétique Fine', 'category': 'SANDER', 'price': 59.9, 'stock': 0}]
    payload = {'manifest': manifest, 'products': products,
               'migrations': [(ROOT / 'migrations' / name).read_text() for name in ('000_create_knowledge_chunks.sql', '001_knowledge_chunks_source_unique.sql')]}
    program = '''
import json, os, sys
import psycopg
from pymongo import MongoClient
from bson.json_util import dumps
from stockpilot.demo_dataset import generate_demo_dataset, PLANNED_INDEXES
payload=json.loads(sys.stdin.readline())
manifest=payload['manifest']
assert manifest['project']=='stockpilot-v133-candidate'
assert os.environ['MONGO_DATABASE']=='stockpilot_candidate_v133'
client=MongoClient(os.environ['MONGO_URI'],serverSelectionTimeoutMS=5000)
assert client.admin.command('hello')['isWritablePrimary']
assert set(client.list_database_names()) <= {'admin','config','local','stockpilot_candidate_v133'}
db=client[os.environ['MONGO_DATABASE']]
marker=db.demo_instance.find_one({'_id':'candidate-v133'})
if marker is not None:
    assert marker['instance_id']==manifest['instance_id']
    with psycopg.connect(os.environ['POSTGRES_URL']) as existing:
        assert existing.execute('SELECT current_database()').fetchone()[0]=='stockpilot_candidate_v133'
        assert existing.execute('SELECT instance_id FROM demo_instance WHERE id=%s',('candidate-v133',)).fetchone()[0]==manifest['instance_id']
    print(json.dumps({'initialization':'existing candidate preserved'}))
    sys.exit(0)
assert not db.list_collection_names(), 'Refusing to initialize a nonempty unmarked candidate database'
dataset=generate_demo_dataset(20261017)
with psycopg.connect(os.environ['POSTGRES_URL']) as pg:
    assert pg.execute('SELECT current_database()').fetchone()[0]=='stockpilot_candidate_v133'
    assert not pg.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
    pg.execute(payload['migrations'][0]); pg.execute(payload['migrations'][1])
    pg.execute('CREATE TABLE demo_instance (id text PRIMARY KEY, instance_id text NOT NULL, project text NOT NULL)')
    pg.execute('INSERT INTO demo_instance VALUES (%s,%s,%s)',('candidate-v133',manifest['instance_id'],manifest['project']))
with client.start_session() as session:
    with session.start_transaction():
        db.products.insert_many(list(dataset.products)+payload['products'],session=session)
        db.orders.insert_many(list(dataset.orders),session=session)
        db.stock_movements.insert_many(list(dataset.stock_movements),session=session)
        db.candidate_seed.insert_one({'_id':'seed-v133','seed':20261017,'base_sha256':dataset.manifest['sha256']},session=session)
        db.demo_instance.insert_one({'_id':'candidate-v133','instance_id':manifest['instance_id'],'project':manifest['project']},session=session)
for name,keys,unique,partial in PLANNED_INDEXES:
    collection='products' if name.startswith('products') else 'stock_movements' if name.startswith('stock_movements') else 'orders'
    options={'name':name,'unique':unique}
    if partial: options['partialFilterExpression']=partial
    db[collection].create_index(list(keys),**options)
print(json.dumps({'initialization':'insert-only','database':db.name,'seed':20261017,'base_sha256':dataset.manifest['sha256'],
    'counts':{name:db[name].count_documents({}) for name in ('products','orders','stock_movements')}}))
'''
    # The payload/source are streamed to the candidate image; no credential appears in argv or output.
    # Python -c consumes stdin only as the data payload.
    running = subprocess.check_output(['docker','ps','-q','--filter',f'label=com.docker.compose.project={PROJECT}',
                                       '--filter','label=com.docker.compose.service=api'], text=True).strip()
    invocation = ('exec','-T','api') if running else ('run','--rm','--no-deps','-T','api')
    result = compose(DEFAULT, *invocation, 'python', '-c', program,
                     input=json.dumps(payload) + '\n', capture_output=True, text=True)
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
