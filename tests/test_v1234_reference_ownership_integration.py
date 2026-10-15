"""Consolidated review: owner-scoped references and read-only recovery cards."""
import asyncio
from uuid import uuid4

import httpx
import pytest

from customer_auth import customer_headers
from test_mongo_transaction_integration import database
from test_postgres_checkpoint_integration import checkpoint_uri
from test_v1233_reference_checkpoint_integration import harness, post, seed, OWNER, SKU
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.domain.enums.intent import Intent
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer

pytestmark = pytest.mark.integration


async def as_customer(api, owner, thread, message):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api, raise_app_exceptions=False),
        base_url='http://isolated-v1234',
    ) as client:
        return await client.post('/assistant', json={'thread_id':thread, 'message':message},
                                 headers=customer_headers(owner))


def test_other_customer_cannot_replay_or_consult_same_wire_thread(database, checkpoint_uri):
    pid, products, orders, transaction, classifier, app = harness(database)
    thread = uuid4().hex
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            api = app(saver)
            classifier.execute.return_value = IntentResult(intent=Intent.ORDER, quantity=1)
            assert (await post(api,thread,'Je veux commander '+SKU)).json()['active_order_confirmation']
            confirmed = (await post(api,thread,'oui')).json()
            order_id = confirmed['created_order_id']
            operation_id = confirmed['pending_order']['operation_id']
        before_products = list(database.products.find({}))
        before_orders = list(database.orders.find({}))
        async with open_checkpointer(checkpoint_uri) as saver:
            api = app(saver)
            classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
            replay = await as_customer(api,'another-customer',thread,'oui')
            assert replay.status_code == 200
            result = replay.json()
            assert result.get('pending_order') is None
            assert result.get('active_order_confirmation') is None
            assert result.get('created_order_id') is None
            assert order_id not in replay.text and operation_id not in replay.text
            classifier.execute.return_value = IntentResult(intent=Intent.ORDER_STATUS, order_id=order_id)
            status = await as_customer(api,'another-customer',thread,'Statut de la commande '+order_id)
            assert status.status_code == 200
            result = status.json()
            assert result['order_status_result'] == 'NOT_FOUND'
            assert result.get('queried_order_status') is None
            assert result.get('created_order_id') is None
            assert OWNER not in status.text and operation_id not in status.text
        assert list(database.products.find({})) == before_products
        assert list(database.orders.find({})) == before_orders
        transaction.products.reserve_stock_sku.assert_called_once()
    asyncio.run(scenario())


def test_foreign_order_reference_is_indistinguishable_from_unknown(database, checkpoint_uri):
    pid, products, orders, transaction, classifier, app = harness(database)
    async def scenario():
        await seed(orders,'FOREIGN-REF-001',owner='private-owner')
        before_products = list(database.products.find({}))
        before_orders = list(database.orders.find({}))
        results = []
        async with open_checkpointer(checkpoint_uri) as saver:
            api = app(saver)
            for reference in ['FOREIGN-REF-001','UNKNOWN-REF-001']:
                classifier.execute.return_value = IntentResult(intent=Intent.ORDER_STATUS,order_id=reference)
                response = await post(api,uuid4().hex,'Commande '+reference)
                assert response.status_code == 200
                state = response.json()
                assert state['intent'] == 'UNKNOWN'
                assert state.get('pending_order') is None and state.get('active_order_confirmation') is None
                assert state.get('order_id') is None and state.get('queried_order_id') is None
                assert 'private-owner' not in response.text
                results.append(state['response'])
            assert results[0] == results[1]
        assert list(database.products.find({})) == before_products
        assert list(database.orders.find({})) == before_orders
        transaction.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


def test_clarification_replay_names_exact_confirmed_order_not_queried_order(database, checkpoint_uri):
    pid, products, orders, transaction, classifier, app = harness(database)
    thread = uuid4().hex
    async def scenario():
        await seed(orders,'OTHER-ORDER-001')
        async with open_checkpointer(checkpoint_uri) as saver:
            api = app(saver)
            classifier.execute.return_value = IntentResult(intent=Intent.ORDER,quantity=1)
            await post(api,thread,'Je veux commander '+SKU)
            confirmed = (await post(api,thread,'oui')).json()
            order_a = confirmed['created_order_id']
            operation_a = confirmed['pending_order']['operation_id']
            before_products = list(database.products.find({}))
            before_orders = list(database.orders.find({}))
            classifier.execute.return_value = IntentResult(intent=Intent.ORDER_STATUS,order_id='OTHER-ORDER-001')
            status = (await post(api,thread,'Statut de la commande OTHER-ORDER-001')).json()
            assert status['queried_order_id'] == 'OTHER-ORDER-001'
            classifier.execute.return_value = IntentResult(intent=Intent.ORDER_STATUS,order_id='UNKNOWN-REF-001')
            unclear = (await post(api,thread,'Commande UNKNOWN-REF-001')).json()
            assert unclear['intent'] == 'UNKNOWN' and 'Précisez' in unclear['response']
        async with open_checkpointer(checkpoint_uri) as saver:
            replay = (await post(app(saver),thread,'oui')).json()
            assert replay['confirmation_handled'] is True
            assert replay['order_status'] == 'CONFIRMED'
            assert replay['intent'] == 'ORDER'
            assert replay['created_order_id'] == order_a
            assert replay['pending_order']['operation_id'] == operation_a
            assert replay['pending_order']['customer_id'] == OWNER
            assert replay.get('queried_order_id') is None
            assert replay.get('queried_order_status') is None
            assert replay.get('order_status_result') is None
            assert order_a in replay['response'] and 'déjà confirmée' in replay['response']
            assert 'OTHER-ORDER-001' not in replay['response']
            assert list(database.products.find({})) == before_products
            assert list(database.orders.find({})) == before_orders
            transaction.products.reserve_stock_sku.assert_called_once()
    asyncio.run(scenario())
