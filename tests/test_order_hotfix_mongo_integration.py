"""Real transaction safety on a fresh UUID database from MONGO_TEST_URI."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from langgraph.checkpoint.memory import MemorySaver

import pytest

from test_mongo_transaction_integration import database
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.order_idempotency import IdempotencyConflictError
from stockpilot.application.workflows.nodes import create_order_node, create_order_confirmation_node
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction

pytestmark = pytest.mark.integration

def services(db):
    repository = MongoProductRepository(db)
    return (create_order_node(PrepareOrderUseCase(CheckStockUseCase(repository), ResolveProductUseCase(repository))),
            create_order_confirmation_node(FinalizeOrderUseCase(MongoOrderTransaction(db))))

def snapshot(db):
    return {name: list(db[name].find().sort('_id', 1)) for name in ('products', 'orders', 'stock_movements')}


@pytest.mark.parametrize('branch', ['PRODUCT_ADVICE', 'STOCK_CHECK', 'UNKNOWN'])
def test_v1226_retired_proposal_never_mutates_real_mongo(database, branch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from stockpilot.application.dto.intent_result import IntentResult
    from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase
    from stockpilot.application.workflows.stockpilot_workflow import build_graph
    from stockpilot.domain.enums.intent import Intent

    database.products.update_one({'sku': 'TEST-BOSCH-X100'}, {'$set': {'category': 'DRILL', 'price': 10.}})
    repository = MongoProductRepository(database)
    stock = CheckStockUseCase(repository)
    classifier = SimpleNamespace(execute=AsyncMock(side_effect=lambda message:
        IntentResult(intent=Intent.ORDER, product_name='Bosch X100', quantity=3)
        if message.startswith('Commande') else IntentResult(
            intent=Intent(branch) if message != 'oui confirme' else Intent.UNKNOWN,
            product_name='Bosch X100' if branch == 'STOCK_CHECK' else None)))
    advice = CatalogueAdviceUseCase(SimpleNamespace(find_advice_candidates=AsyncMock()),
                                   SimpleNamespace(generate=AsyncMock()))
    graph = build_graph(classifier, stock, SimpleNamespace(execute=AsyncMock()),
        PrepareOrderUseCase(stock, ResolveProductUseCase(repository)),
        FinalizeOrderUseCase(MongoOrderTransaction(database)), checkpointer=MemorySaver(),
        get_order_status=SimpleNamespace(execute=AsyncMock()), catalogue_advice=advice)
    config = {'configurable': {'thread_id': 'v1226-real-mongo'}}
    def send(message):
        return asyncio.run(graph.ainvoke({'message': message, 'customer_id': 'owner'}, config))
    before = snapshot(database)
    prepared = send('Commande 3 TEST-BOSCH-X100')
    assert prepared['active_order_confirmation'] is not None
    assert prepared['pending_order']['product_document_id'] == str(database.products.find_one({'sku': 'TEST-BOSCH-X100'})['_id'])
    retired = send('Conseille-moi une perceuse dans mon budget')
    assert retired['pending_order'] is None and retired['active_order_confirmation'] is None
    after = send('oui confirme')
    assert after['confirmation_handled'] is False
    assert snapshot(database) == before


@pytest.mark.parametrize('proof', ['missing', 'other-operation'])
def test_v1226_ineligible_checkpoint_has_only_readonly_recovery(database, proof):
    database.products.update_one({'sku': 'TEST-BOSCH-X100'}, {'$set': {'category': 'DRILL', 'price': 10.}})
    prepare, confirm = services(database)
    initial = {'message': 'Commande 1 TEST-BOSCH-X100', 'product_name': 'Bosch X100',
               'quantity': 1, 'customer_id': 'owner'}
    prepared = asyncio.run(prepare(initial))
    if proof == 'missing':
        prepared.pop('active_order_confirmation')
    else:
        prepared['active_order_confirmation']['operation_id'] = 'other-operation'
    before = snapshot(database)
    result = asyncio.run(confirm({**initial, **prepared, 'message': 'oui'}))
    assert result['order_status'] == 'PRODUCT_IDENTITY_REQUIRED'
    assert result['active_order_confirmation'] is None
    assert snapshot(database) == before

@pytest.mark.parametrize('quantity', [0, -1, -71, 1.5, True])
def test_invalid_quantity_has_no_real_mongo_mutation(database, quantity):
    node, _ = services(database)
    before = snapshot(database)
    result = asyncio.run(node({'message': 'Commander TEST-BOSCH-X100', 'product_name': 'Bosch X100', 'quantity': quantity}))
    assert result['order_status'] == 'INVALID_QUANTITY' and result['pending_order'] is None
    assert snapshot(database) == before

def test_real_concurrent_confirmation_then_restored_terminal_replay(database):
    database.products.insert_one({'sku': 'HOTFIX-0001', 'name': 'Hotfix Drill', 'category': 'DRILL', 'price': 10., 'stock': 2})
    prepare, confirm = services(database)
    initial = {'message': 'Je veux commander 1 HOTFIX-0001', 'product_name': 'Hotfix Drill', 'quantity': 1, 'customer_id': 'owner'}
    pending = asyncio.run(prepare(initial))
    barrier = Barrier(2)
    def execute(_):
        barrier.wait(timeout=10)
        return asyncio.run(confirm({**initial, **deepcopy(pending), 'message': 'oui'}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    assert results[0]['created_order_id'] == results[1]['created_order_id']
    assert all(result['order_status'] == 'CONFIRMED' for result in results)
    assert database.orders.count_documents({}) == 1
    product = database.products.find_one({'sku': 'HOTFIX-0001'})
    assert product['stock'] == 1
    before = snapshot(database)
    _, restored_node = services(database)
    replay = asyncio.run(restored_node({**initial, **results[0], 'message': 'oui, confirme'}))
    assert replay['created_order_id'] == results[0]['created_order_id']
    assert replay['pending_order']['operation_id'] == pending['pending_order']['operation_id']
    assert 'déjà confirmée' in replay['response']
    assert snapshot(database) == before
    order = database.orders.find_one({})
    assert order['reserved_product_id'] == product['_id']
    assert order['product_document_id'] == str(product['_id'])

def test_terminal_checkpoint_with_missing_operation_cannot_create_order(database):
    database.products.insert_one({'sku': 'MISSING-0001', 'name': 'Missing Operation Drill', 'category': 'DRILL', 'price': 10., 'stock': 2})
    prepare, confirm = services(database)
    initial = {'message': 'Je veux commander 1 MISSING-0001', 'product_name': 'Missing Operation Drill', 'quantity': 1, 'customer_id': 'owner'}
    state = asyncio.run(prepare(initial))
    state['pending_order']['status'] = 'CONFIRMED'
    before = snapshot(database)
    result = asyncio.run(confirm({**initial, **state, 'order_status': 'CONFIRMED', 'created_order_id': 'missing', 'message': 'oui'}))
    assert result['order_status'] == 'ORDER_OUTCOME_UNKNOWN'
    assert result['created_order_id'] is None
    assert snapshot(database) == before

def test_confirmed_replay_with_wrong_existing_product_identity_is_rejected(database):
    ids = database.products.insert_many([
        {'sku': 'IDENTITY-0001', 'name': 'Identity Drill', 'category': 'DRILL', 'price': 10., 'stock': 2},
        {'sku': 'IDENTITY-0002', 'name': 'Other Drill', 'category': 'DRILL', 'price': 20., 'stock': 2},
    ]).inserted_ids
    prepare, confirm = services(database)
    initial = {'message': 'Je veux commander 1 IDENTITY-0001', 'product_name': 'Identity Drill', 'quantity': 1, 'customer_id': 'owner'}
    pending = asyncio.run(prepare(initial))
    confirmed = asyncio.run(confirm({**initial, **pending, 'message': 'oui'}))
    confirmed['pending_order']['product_document_id'] = str(ids[1])
    before = snapshot(database)
    with pytest.raises(IdempotencyConflictError):
        asyncio.run(confirm({**initial, **confirmed, 'message': 'oui'}))
    assert snapshot(database) == before


@pytest.mark.parametrize('lexeme', ['1.5', '1,5', '2.75', '-1', '0'])
def test_wrong_extraction_cannot_mutate_real_mongo(database, lexeme):
    prepare, _ = services(database)
    before = snapshot(database)
    result = asyncio.run(prepare({'message': f'Je veux commander {lexeme} TEST-BOSCH-X100',
        'product_name': 'Bosch X100', 'quantity': 1, 'customer_id': 'owner'}))
    assert result['order_status'] == 'INVALID_QUANTITY' and result['pending_order'] is None
    assert snapshot(database) == before


@pytest.mark.parametrize('quantity', [1.5, True, '1', 0, -1])
def test_corrupt_checkpoint_never_reserves_real_stock(database, quantity):
    database.products.update_one({'sku': 'TEST-BOSCH-X100'}, {'$set': {'category': 'DRILL', 'price': 10.}})
    prepare, confirm = services(database)
    state = asyncio.run(prepare({'message': 'Je veux commander 1 TEST-BOSCH-X100',
        'product_name': 'Bosch X100', 'quantity': 1, 'customer_id': 'owner'}))
    state['pending_order']['quantity'] = quantity
    before = snapshot(database)
    result = asyncio.run(confirm({**state, 'message': 'oui confirme', 'customer_id': 'owner'}))
    assert result['order_status'] == 'INVALID_QUANTITY'
    assert snapshot(database) == before


def test_http_exact_incident_and_corrected_confirmation_use_isolated_database(database, monkeypatch):
    from fastapi.testclient import TestClient
    from customer_auth import configure_customer_auth, customer_headers
    from stockpilot.main import app
    from stockpilot.application.workflows.stockpilot_workflow import build_graph
    from stockpilot.application.dto.intent_result import IntentResult
    from stockpilot.domain.enums.intent import Intent
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    database.products.insert_one({'sku': 'DEMO-DRILL-0002', 'name': 'Synthetic Drill 18V',
        'category': 'DRILL', 'price': 10., 'stock': 10})
    repository = MongoProductRepository(database)
    classifier = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(intent=Intent.ORDER, quantity=1)))
    stock = CheckStockUseCase(repository)
    graph = build_graph(classifier, stock, SimpleNamespace(execute=AsyncMock()),
        PrepareOrderUseCase(stock, ResolveProductUseCase(repository)),
        FinalizeOrderUseCase(MongoOrderTransaction(database)),
        get_order_status=SimpleNamespace(execute=AsyncMock()),
        checkpointer=MemorySaver())
    configure_customer_auth(monkeypatch, app)
    monkeypatch.setattr(app.state, 'stockpilot_workflow', graph)
    client = TestClient(app)
    def send(message):
        response = client.post('/assistant', headers=customer_headers(), json={'thread_id': 'incident', 'message': message})
        assert response.status_code == 200
        return response.json()
    before = snapshot(database)
    rejected = send('Je veux commander 1.5 DEMO-DRILL-0002')
    assert rejected['order_status'] == 'INVALID_QUANTITY' and rejected['pending_order'] is None
    assert snapshot(database) == before
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, quantity=2)
    prepared = send('Je veux commander 2 DEMO-DRILL-0002')
    product = database.products.find_one({'sku': 'DEMO-DRILL-0002'})
    assert prepared['pending_order']['product_document_id'] == str(product['_id'])
    assert snapshot(database) == before
    confirmed = send('oui confirme')
    persisted = database.orders.find_one({'order_id': confirmed['created_order_id']})
    assert persisted['reserved_product_id'] == product['_id']
    assert persisted['quantity'] == 2 and persisted['operation_id'] == prepared['pending_order']['operation_id']
    assert database.products.find_one({'_id': product['_id']})['stock'] == 8
    after = snapshot(database)
    replay = send('oui confirme')
    assert replay['created_order_id'] == confirmed['created_order_id']
    assert snapshot(database) == after


@pytest.mark.parametrize('phrase', ['1.5abc','1,5abc','1.5-DEMO-DRILL-0002',
    'une paire de','un lot de','un couple de','exactement une douzaine de',
    'deux-mille','deux voire trois'])
def test_v1224_unsafe_source_has_no_mongo_order_or_reservation(database,phrase):
    prepare,_=services(database)
    before=snapshot(database)
    result=asyncio.run(prepare({'message':f'Commande {phrase} TEST-BOSCH-X100',
        'product_name':'Bosch X100','quantity':1,'customer_id':'owner'}))
    assert result['order_status']=='INVALID_QUANTITY' and result['pending_order'] is None
    assert snapshot(database)==before


@pytest.mark.parametrize('phrase,quantity', [('une douzaine de',12),('une demi-douzaine de',6)])
def test_v1224_exact_lexical_quantity_confirms_once_on_dedicated_product(database,phrase,quantity):
    product_id=database.products.insert_one({'sku':'LEXICAL-SCREW-0001','name':'Synthetic Screws',
        'category':'DRILL','price':1.,'stock':30}).inserted_id
    prepare,confirm=services(database)
    initial={'message':f'Commande {phrase} LEXICAL-SCREW-0001',
        'product_name':'Synthetic Screws','quantity':quantity,'customer_id':'owner'}
    pending=asyncio.run(prepare(initial))
    assert pending['pending_order']['quantity']==quantity
    assert pending['pending_order']['product_document_id']==str(product_id)
    assert database.products.find_one({'_id':product_id})['stock']==30
    state=asyncio.run(confirm({**initial,**pending,'message':'oui confirme'}))
    before=snapshot(database)
    replay=asyncio.run(confirm({**initial,**state,'message':'oui confirme'}))
    assert replay['created_order_id']==state['created_order_id']
    assert snapshot(database)==before and database.orders.count_documents({})==1
    assert database.products.find_one({'_id':product_id})['stock']==30-quantity
