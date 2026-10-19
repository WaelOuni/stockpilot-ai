"""HTTP and persistent proofs on the explicitly guarded, disposable CI services."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI

from customer_auth import configure_customer_auth
from evals.fixtures.catalogue_advice import advice_graph
from test_mongo_transaction_integration import database  # noqa: F401
from test_postgres_checkpoint_integration import checkpoint_uri  # noqa: F401
from test_v1233_reference_checkpoint_integration import harness, post
from test_environment_language_regressions import NAME, NAMED, ORDER, SKU
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.domain.enums.intent import Intent
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer
from stockpilot.presentation.api.routes.assistant import router

pytestmark = pytest.mark.integration


def setup(database):
    _, products, orders, tx, classifier, app = harness(database)
    database.products.insert_many([
        {'sku': SKU, 'name': NAME, 'category': 'DRILL', 'price': 52.69, 'stock': 74},
        {'sku': 'UNDER', 'name': 'Perceuse Sous Plafond', 'category': 'DRILL', 'price': 49.99, 'stock': 2},
        {'sku': 'BOUND', 'name': 'Perceuse Au Plafond', 'category': 'DRILL', 'price': 50., 'stock': 3},
    ])
    return products, orders, tx, classifier, app


def test_exact_advice_reports_use_mongo_prices_and_persist_strict_criteria(database, checkpoint_uri):
    products, _, _, _, _ = setup(database)
    # Include the harness products in the expected query; every returned fact
    # must match the current catalogue, independent of its top-three prices.
    before = list(database.products.find({}))
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            graph, _, llm, forbidden = advice_graph(products, saver)
            llm.classify_intent.return_value = IntentResult(intent=Intent.STOCK_CHECK, budget=999)
            api = FastAPI()
            api.include_router(router)
            configure_customer_auth(None, api)
            api.state.stockpilot_workflow = graph
            for message, bound in [('Quelle perceuse est disponible ?', None),
                                   ('Quelle perceuse est disponible moins 50 ?', 50)]:
                result = (await post(api, uuid4().hex, message)).json()
                query = {'category': 'DRILL', 'stock': {'$gt': 0}}
                if bound is not None:
                    query['price'] = {'$lt': bound}
                expected = list(database.products.find(query).sort([('price', 1), ('sku', 1)]).limit(3))
                assert [p['sku'] for p in result['recommendations']] == [p['sku'] for p in expected]
                for card, stored in zip(result['recommendations'], expected):
                    assert (card['product_name'], card['category'], card['price'], card['stock']) == (
                        stored['name'], stored['category'], stored['price'], stored['stock'])
                assert result['last_advice_criteria']['maximum'] == bound
                assert result['last_advice_criteria']['exclusive_maximum'] is (bound is not None)
                assert all(p['stock'] > 0 and (bound is None or p['price'] < bound)
                           for p in result['recommendations'])
            named = (await post(api, uuid4().hex, NAMED)).json()
            assert [p['sku'] for p in named['recommendations']] == [SKU]
            assert named['recommendations'][0]['price'] == 52.69
            forbidden.execute.assert_not_awaited()
        assert list(database.products.find({})) == before and database.orders.count_documents({}) == 0
    asyncio.run(scenario())


def test_named_numeric_model_prepares_persists_confirms_once_by_exact_sku(database, checkpoint_uri):
    _, _, tx, classifier, app = setup(database)
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name=NAME, quantity=1)
    thread = uuid4().hex
    before = list(database.products.find({}))
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            first = (await post(app(saver), thread, ORDER)).json()
            assert first['order_status'] == 'PENDING_CONFIRMATION'
            pending = first['pending_order']
            assert pending['quantity'] == 1 and pending['product_sku'] == SKU
            assert pending['product_document_id'] == str(database.products.find_one({'sku': SKU})['_id'])
            assert pending['quantity_evidence']['message'] == ORDER
            assert ORDER[pending['quantity_evidence']['start']:pending['quantity_evidence']['end']] == '1'
            assert list(database.products.find({})) == before
            assert database.orders.count_documents({}) == 0
        classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
        async with open_checkpointer(checkpoint_uri) as saver:
            confirmed = (await post(app(saver), thread, 'oui confirme')).json()
            assert confirmed['order_status'] == 'CONFIRMED'
            assert database.products.find_one({'sku': SKU})['stock'] == 73
            stored = database.orders.find_one({'operation_id': pending['operation_id']})
            assert stored['quantity'] == 1 and stored['product_sku'] == SKU
            after = list(database.products.find({}))
        async with open_checkpointer(checkpoint_uri) as saver:
            replay = (await post(app(saver), thread, 'oui confirme')).json()
            assert replay['created_order_id'] == confirmed['created_order_id']
            assert list(database.products.find({})) == after
            assert database.orders.count_documents({}) == 1
        tx.products.reserve_stock_sku.assert_called_once()
    asyncio.run(scenario())


@pytest.mark.parametrize('count', ['-1', '0', '1.5', '1,5', '1 000', 'un et demi', 'plusieurs'])
def test_bad_count_disarms_named_preparation_across_checkpoint_reopen(database, checkpoint_uri, count):
    _, _, tx, classifier, app = setup(database)
    thread = uuid4().hex
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name=NAME, quantity=1)
    before = list(database.products.find({}))
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            pending = (await post(app(saver), thread, ORDER)).json()
            assert pending['order_status'] == 'PENDING_CONFIRMATION'
            bad = (await post(app(saver), thread, f'Commande-moi {count} {NAME}')).json()
            assert bad['order_status'] == 'INVALID_QUANTITY'
            assert bad.get('pending_order') is None and bad.get('active_order_confirmation') is None
        classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
        async with open_checkpointer(checkpoint_uri) as saver:
            yes = (await post(app(saver), thread, 'oui confirme')).json()
            assert not yes.get('created_order_id')
        assert list(database.products.find({})) == before and database.orders.count_documents({}) == 0
        tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize('intent', [Intent.PRODUCT_ADVICE, Intent.STOCK_CHECK])
def test_conflicting_named_classification_disarms_durably(database, checkpoint_uri, intent):
    _, _, tx, classifier, app = setup(database)
    thread = uuid4().hex
    before = list(database.products.find({}))
    async def scenario():
        classifier.execute.return_value = IntentResult(intent=Intent.ORDER, product_name=NAME, quantity=1)
        async with open_checkpointer(checkpoint_uri) as saver:
            assert (await post(app(saver), thread, ORDER)).json()['order_status'] == 'PENDING_CONFIRMATION'
            classifier.execute.return_value = IntentResult(intent=intent, product_name=NAME, quantity=1)
            bad = (await post(app(saver), thread, ORDER)).json()
            assert bad['order_status'] == 'INVALID_QUANTITY' and bad.get('pending_order') is None
        classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
        async with open_checkpointer(checkpoint_uri) as saver:
            assert not (await post(app(saver), thread, 'oui confirme')).json().get('created_order_id')
        assert list(database.products.find({})) == before and database.orders.count_documents({}) == 0
        tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())
