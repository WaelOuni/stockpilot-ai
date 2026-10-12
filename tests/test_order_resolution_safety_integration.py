"""Regression coverage for document identity, user evidence and compensation."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pymongo.errors import ConnectionFailure
from pymongo.synchronous.client_session import ClientSession

from test_mongo_transaction_integration import database
from test_order_resolution_mongo_integration import catalogue, services
from stockpilot.application.order_idempotency import IdempotencyConflictError
from stockpilot.application.use_cases.resolve_product import ResolutionStatus
from stockpilot.application.workflows.nodes import create_order_node
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction

pytestmark = pytest.mark.integration

def run(awaitable):
    return asyncio.run(awaitable)


def test_recreated_same_sku_must_not_reserve_new_document(catalogue):
    _, prepare, finalize = services(catalogue)
    prepared = run(prepare.execute('SP11-SCR-0081'))
    old = catalogue.products.find_one({'sku': prepared.product_sku})
    catalogue.products.delete_one({'_id': old['_id']})
    replacement = catalogue.products.insert_one({
        'sku': old['sku'], 'name': 'Different product recreated under same SKU',
        'category': 'DRILL', 'price': 999.0, 'stock': 10,
    }).inserted_id
    result = run(finalize.execute(prepared.product_name, 1, prepared.operation_id, 'alice', product_sku=prepared.product_sku, product_document_id=prepared.product_document_id))
    persisted = catalogue.orders.find_one({'operation_id': prepared.operation_id})
    assert result is None, 'Different Mongo _id with the same SKU must be refused'
    assert catalogue.products.find_one({'_id': replacement})['stock'] == 10


def test_short_model_name_cannot_drop_user_voltage(catalogue):
    resolver, _, _ = services(catalogue)
    catalogue.products.insert_many([
        {'sku': 'SHORT-NAME-001', 'name': 'Bosch D461', 'category': 'DRILL', 'price': 10., 'stock': 10},
        {'sku': 'CORRECT-NAME-001', 'name': 'Bosch Tournevis D461 12V', 'category': 'DRILL', 'price': 10., 'stock': 10},
    ])
    result = run(resolver.execute('Bosch D461', 'Je veux commander 1 Bosch D461 12V'))
    assert result.product is None or result.product.sku != 'SHORT-NAME-001', 'The LLM may not erase user-specified 12V'


def test_duplicate_names_do_not_prepare_or_reserve(catalogue):
    resolver, prepare, _ = services(catalogue)
    catalogue.products.insert_one({'sku': 'DUP-NAME-001', 'name': 'Bosch DEMO06406657',
                                   'category': 'DRILL', 'stock': 20, 'price': 10.})
    before = list(catalogue.products.find({}))
    result = run(create_order_node(prepare)({'message': 'Commander Bosch DEMO06406657', 'product_name': 'Bosch DEMO06406657'}))
    assert result['order_status'] == 'AMBIGUOUS' and result['pending_order'] is None
    assert catalogue.orders.count_documents({}) == 0
    assert list(catalogue.products.find({})) == before


def test_unknown_explicit_sku_matching_a_real_name_still_refused(catalogue):
    resolver, _, _ = services(catalogue)
    catalogue.products.insert_one({'sku': 'REAL-001', 'name': 'DEMO-DRILL-9999',
                                   'category': 'DRILL', 'stock': 20, 'price': 10.})
    result = run(resolver.execute('DEMO-DRILL-9999', 'Commander DEMO-DRILL-9999'))
    assert result.status == ResolutionStatus.NOT_FOUND and result.product is None


def test_same_operation_other_quantity_conflicts_for_sku_order(catalogue):
    _, prepare, finalize = services(catalogue)
    prepared = run(prepare.execute('SP11-SCR-0081'))
    run(finalize.execute(prepared.product_name, 1, prepared.operation_id, 'alice', product_sku=prepared.product_sku, product_document_id=prepared.product_document_id))
    with pytest.raises(IdempotencyConflictError):
        run(finalize.execute(prepared.product_name, 2, prepared.operation_id, 'alice', product_sku=prepared.product_sku, product_document_id=prepared.product_document_id))
    assert catalogue.products.find_one({'sku': prepared.product_sku})['stock'] == 9


def test_concurrent_confirmations_after_lost_commit_response_reserve_once(catalogue, monkeypatch):
    _, prepare, finalize = services(catalogue)
    prepared = run(prepare.execute('SP11-SCR-0081', 2))
    original_commit = ClientSession.commit_transaction
    original_reconcile = MongoOrderTransaction._reconcile
    commits = []
    def lost_response(session):
        original_commit(session)
        commits.append(True)
        raise ConnectionFailure('review fixture: commit response lost')
    # Force the first caller to observe an unknown result despite a successful commit.
    def inaccessible_reconciliation(self, order):
        if commits:
            from stockpilot.application.order_transaction_errors import OrderOutcomeUnknownError
            raise OrderOutcomeUnknownError('review fixture: network unavailable for reconcile')
        return original_reconcile(self, order)
    monkeypatch.setattr(ClientSession, 'commit_transaction', lost_response)
    monkeypatch.setattr(MongoOrderTransaction, '_reconcile', inaccessible_reconciliation)
    from stockpilot.application.order_transaction_errors import OrderOutcomeUnknownError
    with pytest.raises(OrderOutcomeUnknownError):
        run(finalize.execute(prepared.product_name, 2, prepared.operation_id, 'alice', product_sku=prepared.product_sku, product_document_id=prepared.product_document_id))
    monkeypatch.setattr(ClientSession, 'commit_transaction', original_commit)
    monkeypatch.setattr(MongoOrderTransaction, '_reconcile', original_reconcile)
    barrier = Barrier(4)
    def retry(_):
        barrier.wait(timeout=10)
        return run(FinalizeOrderUseCase(MongoOrderTransaction(catalogue)).execute(
            prepared.product_name, 2, prepared.operation_id, 'alice', product_sku=prepared.product_sku, product_document_id=prepared.product_document_id))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(retry, range(4)))
    assert commits and all(result == results[0] for result in results)
    assert catalogue.products.find_one({'sku': prepared.product_sku})['stock'] == 8
    assert catalogue.orders.count_documents({'operation_id': prepared.operation_id}) == 1


def test_legacy_compensation_without_identity_must_not_credit_replacement(catalogue):
    from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
    from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
    _, _, finalize = services(catalogue)
    name = 'Bosch DEMO06406657'
    original = catalogue.products.find_one({'sku': 'SHORT-001'})
    order = run(finalize.execute(name, 1, str(uuid4()), 'alice'))
    catalogue.orders.update_one({'order_id': order.order_id}, {'$unset': {'reserved_product_id': ''}})
    catalogue.products.delete_one({'_id': original['_id']})
    replacement_id = catalogue.products.insert_one({'sku': 'OTHER-PRODUCT-001', 'name': name,
                            'stock': 20, 'category': 'DRILL', 'price': 100.}).inserted_id
    rejected = False
    try:
        run(CancelOrderUseCase(MongoOrderRepository(catalogue)).execute(order.order_id))
    except RuntimeError:
        rejected = True
    stock = catalogue.products.find_one({'_id': replacement_id})['stock']
    assert rejected, 'A current unique name cannot prove a historical stock reservation identity'
    assert stock == 20


def test_document_identity_persisted_but_not_exposed(catalogue):
    from stockpilot.application.workflows.nodes import create_order_confirmation_node
    from stockpilot.presentation.api.schemas.order import OrderResponse
    from stockpilot.presentation.api.schemas.product import ProductResponse
    _, prepare, finalize = services(catalogue)
    state = run(create_order_node(prepare)({
        'message': 'Commander SP11-SCR-0081', 'product_name': 'SP11-SCR-0081', 'customer_id': 'alice',
    }))
    pending = state['pending_order']
    assert pending['product_document_id'] == str(catalogue.products.find_one({'sku': 'SP11-SCR-0081'})['_id'])
    assert pending['product_document_id'] not in state['response']
    result = run(create_order_confirmation_node(finalize)({**state, 'message': 'oui', 'customer_id': 'alice'}))
    doc = catalogue.orders.find_one({'order_id': result['created_order_id']})
    assert doc['product_document_id'] == pending['product_document_id'] == str(doc['reserved_product_id'])
    assert pending['product_document_id'] not in result['response']
    order = run(finalize.execute(pending['product_name'], 1, pending['operation_id'], 'alice',
                                product_sku=pending['product_sku'], product_document_id=pending['product_document_id']))
    assert 'product_document_id' not in OrderResponse.model_validate(order).model_dump()
    product = run(services(catalogue)[0].execute('SP11-SCR-0081')).product
    assert 'document_id' not in ProductResponse.from_product(product).model_dump()


def test_sku_only_checkpoint_requires_repreparation_or_persisted_replay(catalogue):
    from stockpilot.application.workflows.nodes import create_order_confirmation_node
    _, prepare, finalize = services(catalogue)
    prepared = run(prepare.execute('SP11-SCR-0081'))
    pending = {'product_name': prepared.product_name, 'product_sku': prepared.product_sku,
               'quantity': 1, 'operation_id': prepared.operation_id,
               'status': 'PENDING_CONFIRMATION', 'customer_id': 'alice'}
    state = {'message': 'oui', 'pending_order': pending, 'order_status': 'PENDING_CONFIRMATION', 'customer_id': 'alice'}
    node = create_order_confirmation_node(finalize)
    assert run(node(state))['order_status'] == 'PRODUCT_IDENTITY_REQUIRED'
    assert catalogue.orders.count_documents({}) == 0
    assert catalogue.products.find_one({'sku': prepared.product_sku})['stock'] == 10
    created = run(finalize.execute(prepared.product_name, 1, prepared.operation_id, 'alice',
                                  product_sku=prepared.product_sku, product_document_id=prepared.product_document_id))
    assert run(node(state))['created_order_id'] == created.order_id
    assert catalogue.products.find_one({'sku': prepared.product_sku})['stock'] == 9


@pytest.mark.parametrize('terms,status,expected_sku', [
    ('Bosch D461 12V', 'RESOLVED', 'REVIEW-12V'),
    ('Bosch D461 24V', 'RESOLVED', 'REVIEW-24V'),
    ('Bosch D461 Pro', 'RESOLVED', 'REVIEW-PRO'),
    ('Bosch D461 Max', 'NOT_FOUND', None),
    ('Bosch D461', 'AMBIGUOUS', None),
])
def test_explicit_user_discriminants_survive_model_shortening(catalogue, terms, status, expected_sku):
    # Remove the fixture's D461 product by its explicit fixture ID to get a known candidate set.
    doc = catalogue.products.find_one({'sku': 'SP11-SCR-0081'})
    catalogue.products.delete_one({'_id': doc['_id']})
    catalogue.products.insert_many([
        {'sku': sku, 'name': name, 'category': 'DRILL', 'stock': 10, 'price': 10.}
        for sku, name in [('REVIEW-SHORT', 'Bosch D461'), ('REVIEW-12V', 'Bosch Tournevis D461 12V'),
                          ('REVIEW-24V', 'Bosch Tournevis D461 24V'), ('REVIEW-PRO', 'Bosch Tournevis D461 Pro')]
    ])
    resolved = run(services(catalogue)[0].execute('Bosch D461', 'Merci de réserver ' + terms))
    assert resolved.status.value == status
    assert (resolved.product.sku if resolved.product else None) == expected_sku
    assert catalogue.orders.count_documents({}) == 0


def test_conflicting_document_identity_on_same_operation_is_rejected(catalogue):
    _, prepare, finalize = services(catalogue)
    prepared = run(prepare.execute('SP11-SCR-0081'))
    run(finalize.execute(prepared.product_name, 1, prepared.operation_id, 'alice',
                         product_sku=prepared.product_sku, product_document_id=prepared.product_document_id))
    with pytest.raises(IdempotencyConflictError):
        run(finalize.execute(prepared.product_name, 1, prepared.operation_id, 'alice',
                             product_sku=prepared.product_sku, product_document_id='507f1f77bcf86cd799439011'))
    assert catalogue.products.find_one({'sku': prepared.product_sku})['stock'] == 9


def test_standalone_name_based_credit_never_writes(catalogue):
    from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
    from stockpilot.application.stock_identity import StockIdentityRequiredError
    before = list(catalogue.products.find({}))
    with pytest.raises(StockIdentityRequiredError):
        run(MongoProductRepository(catalogue).release_stock('Bosch DEMO06406657', 1))
    assert list(catalogue.products.find({})) == before


@pytest.mark.parametrize('variant', ['A', 'Reserve'])
def test_request_words_inside_a_product_phrase_remain_constraints(catalogue, variant):
    catalogue.products.insert_one({'sku': 'CONTRADICTORY-001', 'name': 'Bosch D777 12V',
                                  'category': 'DRILL', 'stock': 10, 'price': 10.})
    result = run(services(catalogue)[0].execute('Bosch D777 12V', f'Commander Bosch D777 {variant} 12V'))
    assert result.status == ResolutionStatus.NOT_FOUND


def test_quantity_continuation_cannot_rebind_recreated_sku(catalogue):
    from stockpilot.application.dto.intent_result import IntentResult
    from stockpilot.domain.enums.intent import Intent
    from stockpilot.application.use_cases.check_stock import CheckStockUseCase
    from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
    from stockpilot.application.workflows.stockpilot_workflow import build_graph
    from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
    from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
    from unittest.mock import AsyncMock
    from langgraph.checkpoint.memory import MemorySaver
    _, prepare, finalize = services(catalogue)
    classifier = SimpleNamespace(execute=AsyncMock(side_effect=[
        IntentResult(intent=Intent.ORDER, product_name=None, quantity=1), IntentResult(intent=Intent.UNKNOWN),
    ]))
    graph = build_graph(classifier, CheckStockUseCase(MongoProductRepository(catalogue)), SimpleNamespace(execute=AsyncMock()),
                        prepare, finalize, MemorySaver(), get_order_status=GetOrderStatusUseCase(MongoOrderRepository(catalogue)))
    config = {'configurable': {'thread_id': uuid4().hex}}
    prepared = run(graph.ainvoke({'message': 'Commander SP11-SCR-0081', 'customer_id': 'alice'}, config))
    old = catalogue.products.find_one({'sku': 'SP11-SCR-0081'})
    catalogue.products.delete_one({'_id': old['_id']})
    new = {k: v for k, v in old.items() if k != '_id'}
    catalogue.products.insert_one(new)
    followup = run(graph.ainvoke({'message': "J'en veux 2 unités", 'customer_id': 'alice'}, config))
    assert followup['order_status'] == 'PRODUCT_IDENTITY_REQUIRED'
    assert followup['pending_order'] is None
    assert catalogue.products.find_one({'sku': 'SP11-SCR-0081'})['stock'] == 10
    assert catalogue.orders.count_documents({}) == 0
