"""Real transaction safety on a fresh UUID database from MONGO_TEST_URI."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

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
