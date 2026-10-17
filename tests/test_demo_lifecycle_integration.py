"""Opt-in lifecycle test on NEW disposable instances, never the developer's stores.

Start scripts/tests/demo-compose.yml first. Endpoint constants are deliberately
fixed; production identity guards stay unchanged. Reference providers are injected
only in this test process and its isolated launcher subprocess.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from uuid import uuid4

from bson import json_util
import httpx
import psycopg
from psycopg.conninfo import make_conninfo
from pymongo import MongoClient
import pytest

from stockpilot.demo_dataset import DEMO_DATABASE
from stockpilot.demo_runtime import preflight_demo
from stockpilot.demo_lifecycle import managed_demo, demo_resources, reset_workspace
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
from stockpilot.application.use_cases.manage_products import CreateProductUseCase, UpdateProductMetadataUseCase
from stockpilot.application.use_cases.adjust_product_stock import AdjustProductStockUseCase
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.presentation.api.admin_auth import password_verifier

pytestmark = [pytest.mark.integration, pytest.mark.integration_optional]
ROOT = Path(__file__).resolve().parents[1]
MONGO = 'mongodb://127.0.0.1:28027/?replicaSet=rs0&directConnection=true'
POSTGRES = 'postgresql://lifecycle:lifecycle@127.0.0.1:56432/stockpilot_demo'


@pytest.fixture
def temporary_demo(monkeypatch):
    # Never default to a developer connection and never clear an existing fixture.
    if 'DEMO_LIFECYCLE_TEST_MONGO_URI' not in os.environ:
        pytest.skip('Dedicated ephemeral lifecycle services are not explicitly enabled')
    assert os.environ['DEMO_LIFECYCLE_TEST_MONGO_URI'] == MONGO
    assert os.environ['DEMO_LIFECYCLE_TEST_POSTGRES_URI'] == POSTGRES
    monkeypatch.setenv('MONGO_URI', MONGO)
    import scripts.prepare_demo_postgres as provision
    monkeypatch.setattr(provision, 'demo_uri', lambda: POSTGRES)
    monkeypatch.setattr(provision, 'existing_server_uri', lambda: make_conninfo(POSTGRES, dbname='postgres'))
    settings = Settings(mongo_uri=MONGO, mongo_database=DEMO_DATABASE,
                        postgres_url=POSTGRES, langgraph_checkpoint_db_uri=None)
    with MongoClient(MONGO) as client, psycopg.connect(make_conninfo(POSTGRES, dbname='postgres')) as admin:
        assert DEMO_DATABASE not in client.list_database_names(), 'Existing test data: refuse, recreate disposable services'
        assert not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s', (DEMO_DATABASE,)).fetchone()
        yield settings, client[DEMO_DATABASE]


def snapshot(db):
    payload = {name: list(db[name].find({}).sort('_id', 1))
               for name in sorted(db.list_collection_names())}
    return hashlib.sha256(json_util.dumps(payload, sort_keys=True).encode()).hexdigest()


def postgres_snapshot():
    with psycopg.connect(POSTGRES) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        return {name: sorted(map(repr, connection.execute('SELECT * FROM public.' + name).fetchall()))
                for name in ('checkpoint_migrations', 'checkpoints', 'checkpoint_blobs',
                             'checkpoint_writes', 'knowledge_chunks')}


def actual_restart(settings, db):
    before = snapshot(db)
    before_pg = postgres_snapshot()
    environment = dict(os.environ, MONGO_URI=MONGO,
                       STOCKPILOT_DEMO_MONGO_URI=MONGO,
                       STOCKPILOT_DEMO_POSTGRES_URL=POSTGRES,
                       STOCKPILOT_DEMO_CHECKPOINT_URI=POSTGRES,
                       STOCKPILOT_DEMO_NONINTERACTIVE='true', ADMIN_USERNAME='ephemeral-admin',
                       ADMIN_PASSWORD_VERIFIER=password_verifier(secrets.token_urlsafe(32)),
                       CUSTOMER_JWT_SECRET=secrets.token_urlsafe(32),
                       LOG_FILE='', OTEL_ENABLED='false', PYTHONDONTWRITEBYTECODE='1')
    # Bind the independent reference providers to the NEW test instance, not the real container.
    # Assert those exact endpoints before supplying this test-only dependency injection.
    code = '''
import os
from psycopg.conninfo import make_conninfo
import scripts.prepare_demo_postgres as p
uri = os.environ['DEMO_LIFECYCLE_TEST_POSTGRES_URI']
assert uri == 'postgresql://lifecycle:lifecycle@127.0.0.1:56432/stockpilot_demo'
assert os.environ['MONGO_URI'] == 'mongodb://127.0.0.1:28027/?replicaSet=rs0&directConnection=true'
p.demo_uri = lambda: uri
p.existing_server_uri = lambda: make_conninfo(uri, dbname='postgres')
from scripts.run_demo import main
raise SystemExit(main())
'''
    process = subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url='http://127.0.0.1:5175', timeout=2) as client:
            for _ in range(120):
                if process.poll() is not None:
                    pytest.fail('Isolated launcher exited before readiness')
                try:
                    if client.get('/api/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.25)
            else:
                pytest.fail('Isolated launcher readiness timeout')
            assert client.get('/api/products').status_code == 401
            token = subprocess.check_output([
                sys.executable, str(ROOT / 'scripts/create_customer_token.py'),
                'ephemeral-lifecycle-reader', '--expires-in', '1800',
            ], cwd=ROOT, env=environment, text=True, stderr=subprocess.PIPE).strip()
            assert client.get('/api/products', headers={'Authorization': 'Bearer ' + token}).status_code == 200
            assert client.get('/products').status_code == 200
    finally:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=30)
    assert before == snapshot(db), 'Restart changed business contents'
    assert before_pg == postgres_snapshot(), 'Restart changed PostgreSQL contents'
    assert preflight_demo(settings).mongo_summary['workspace'] == 'mutable/persistent'


def test_first_start_business_operations_and_repeated_real_restarts(temporary_demo, monkeypatch):
    settings, db = temporary_demo
    confirmations = []
    monkeypatch.setattr('stockpilot.demo_lifecycle.confirm', confirmations.append)
    with managed_demo(settings):
        assert confirmations == ['INITIALIZE']
        with pytest.raises(ValueError, match='already running'):
            with demo_resources(settings):
                pytest.fail('Second lifecycle acquired the running DEMO lock')
    async def persist_checkpoint():
        from langgraph.checkpoint.base import empty_checkpoint
        from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer
        async with open_checkpointer(POSTGRES) as saver:
            await saver.aput({'configurable': {'thread_id': 'temporary-lifecycle-thread', 'checkpoint_ns': ''}},
                             empty_checkpoint(), {'source': 'input', 'step': -1, 'parents': {}}, {})
    asyncio.run(persist_checkpoint())
    assert len(postgres_snapshot()['checkpoints']) == 1
    actual_restart(settings, db)  # First complete launch, then plain resume.
    actual_restart(settings, db)
    products = MongoProductRepository(db)
    product = db.products.find_one({'demo_dataset_id': {'$exists': True}})
    asyncio.run(UpdateProductMetadataUseCase(products).execute(
        product['sku'], {'name': 'Edited persistent tool', 'price': 42.0, 'category': ProductCategory.OTHER}))
    actual_restart(settings, db)
    assert db.products.find_one({'_id': product['_id']})['name'] == 'Edited persistent tool'
    before_stock = db.products.find_one({'_id': product['_id']})['stock']
    operation = str(uuid4())
    adjuster = AdjustProductStockUseCase(products)
    asyncio.run(adjuster.execute(product['sku'], 5, 'Temporary lifecycle test', operation))
    asyncio.run(adjuster.execute(product['sku'], 5, 'Temporary lifecycle test', operation))
    actual_restart(settings, db)
    assert db.products.find_one({'_id': product['_id']})['stock'] == before_stock + 5
    order = Order(str(uuid4()), str(uuid4()), 'Edited persistent tool', 1,
                  OrderStatus.CREATED, datetime.now(timezone.utc), customer_id='new-test-customer')
    asyncio.run(MongoOrderTransaction(db).execute(order))
    actual_restart(settings, db)
    assert db.products.find_one({'_id': product['_id']})['stock'] == before_stock + 4
    repository = MongoOrderRepository(db)
    asyncio.run(CancelOrderUseCase(repository).execute(order.order_id, customer_id='new-test-customer'))
    asyncio.run(CancelOrderUseCase(repository).execute(order.order_id, customer_id='new-test-customer'))
    actual_restart(settings, db)
    assert db.products.find_one({'_id': product['_id']})['stock'] == before_stock + 5
    assert db.orders.find_one({'order_id': order.order_id})['status'] == 'CANCELLED'
    asyncio.run(CreateProductUseCase(products).execute('NEW-TEMP-SKU', 'New working product', ProductCategory.DRILL, 2.5))
    actual_restart(settings, db)
    assert db.products.find_one({'sku': 'NEW-TEMP-SKU'})['stock'] == 0

    # Test refusal without authorization, not an actual reset (even of temporary data).
    import stockpilot.demo_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, 'confirm', lambda action: (_ for _ in ()).throw(ValueError('Confirmation refused')))
    before = snapshot(db)
    with demo_resources(settings) as resources:
        with pytest.raises(ValueError, match='Confirmation refused'):
            reset_workspace(settings, resources)
    assert snapshot(db) == before
    # A foreign collection is not silently adopted or removed.
    db.create_collection('unknown_store')
    with pytest.raises(ValueError, match='incompatible'):
        preflight_demo(settings)
    assert db.list_collection_names()  # No cleanup/reseed attempted.
