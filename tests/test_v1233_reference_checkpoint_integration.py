"""Real ASGI, isolated Mongo and reopened PostgreSQL; deterministic classifier.

No request reaches a DEMO, registry or Ollama. Only fresh UUID fixture databases
and schemas configured through the explicit test URIs are written.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from customer_auth import configure_customer_auth, customer_headers
from test_mongo_transaction_integration import database
from test_postgres_checkpoint_integration import checkpoint_uri
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase
from stockpilot.application.use_cases.resolve_order_reference import ResolveOrderReferenceUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.enums.intent import Intent
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer
from stockpilot.presentation.api.routes.assistant import router, customer_thread_key

pytestmark=pytest.mark.integration
SKU='DEMO-DRILL-0002'
OWNER='v1233-synthetic'


def harness(database, *, normalize=True):
    product_id=database.products.insert_one({'sku':SKU,'name':'Perceuse Synthétique V015 12V',
        'category':'DRILL','price':1.,'stock':20}).inserted_id
    products=MongoProductRepository(database);orders=MongoOrderRepository(database)
    transaction=MongoOrderTransaction(database)
    transaction.products.reserve_stock_sku=Mock(wraps=transaction.products.reserve_stock_sku)
    classifier=SimpleNamespace(execute=AsyncMock(return_value=IntentResult(intent=Intent.UNKNOWN)))
    reference=ResolveOrderReferenceUseCase(products,orders)
    def app(saver):
        api=FastAPI();api.include_router(router);configure_customer_auth(None,api)
        api.state.stockpilot_workflow=build_graph(classifier,CheckStockUseCase(products),
            SimpleNamespace(execute=AsyncMock()),
            PrepareOrderUseCase(CheckStockUseCase(products),ResolveProductUseCase(products)),
            FinalizeOrderUseCase(transaction),checkpointer=saver,
            get_order_status=GetOrderStatusUseCase(orders),
            resolve_order_reference=reference if normalize else None)
        return api
    return product_id,products,orders,transaction,classifier,app


async def post(api,thread,message):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api,raise_app_exceptions=False),
                                base_url='http://local-v1233') as client:
        return await client.post('/assistant',json={'thread_id':thread,'message':message},
                                 headers=customer_headers(OWNER))


async def seed(orders,reference,owner=OWNER):
    return await orders.create(Order(reference,uuid4().hex,'Fixture historique',1,OrderStatus.CREATED,
        datetime.now(timezone.utc),customer_id=owner))


@pytest.mark.parametrize('message,intent,quantity,order_id,expected',[
    ('Commande 2 '+SKU,Intent.ORDER,2,None,'PENDING_CONFIRMATION'),
    ('Commande '+SKU,Intent.ORDER_STATUS,None,SKU,'PENDING_CONFIRMATION'),
    ('Je veux commander '+SKU,Intent.ORDER,None,None,'PENDING_CONFIRMATION'),
    ('Ajoute 2 '+SKU+' à ma commande',Intent.ORDER,2,None,'INVALID_QUANTITY'),
    ('Je veux acheter 2 '+SKU,Intent.ORDER,2,None,'PENDING_CONFIRMATION'),
    ('Montre-moi la commande ORD-TEST-001',Intent.ORDER_STATUS,None,'ORD-TEST-001','FOUND'),
    ('Où en est ma commande ORD-TEST-001 ?',Intent.ORDER_STATUS,None,'ORD-TEST-001','FOUND'),
    ('Statut de la commande ORD-TEST-001',Intent.ORDER_STATUS,None,'ORD-TEST-001','FOUND'),
    ('Retrouve la commande ORD-TEST-001',Intent.ORDER_STATUS,None,'ORD-TEST-001','FOUND'),
    ('Cherche le produit '+SKU,Intent.STOCK_CHECK,None,None,'stock'),
    ('Stock du produit '+SKU,Intent.STOCK_CHECK,None,None,'stock'),
    ('Confirme la commande',Intent.ORDER,None,None,'INVALID_QUANTITY'),
    ('Commande 2',Intent.ORDER_STATUS,None,'2','clarification'),
    ('Commande ABC-123',Intent.ORDER_STATUS,None,'ABC-123','clarification'),
    ('Commande '+SKU,Intent.ORDER_STATUS,2,SKU,'INVALID_QUANTITY'),
])
def test_http_contract_matrix_without_mutation(database,checkpoint_uri,message,intent,quantity,order_id,expected):
    pid,products,orders,tx,classifier,app=harness(database)
    async def scenario():
        await seed(orders,'ORD-TEST-001')
        before=list(database.products.find({}).sort('_id',1));before_orders=list(database.orders.find({}))
        classifier.execute.return_value=IntentResult(intent=intent,product_name="Perceuse Synthétique V015 12V" if intent==Intent.STOCK_CHECK else None,
                                                    quantity=quantity,order_id=order_id)
        async with open_checkpointer(checkpoint_uri) as saver:
            response=await post(app(saver),uuid4().hex,message)
            assert response.status_code==200
            state=response.json()
            if expected=='PENDING_CONFIRMATION':
                pending=state['pending_order']
                assert pending['quantity']==(quantity or 1)
                assert pending['product_sku']==SKU and pending['product_document_id']==str(pid)
                assert state['active_order_confirmation']['operation_id']==pending['operation_id']
                assert state.get('order_id') is None
            elif expected=='FOUND':
                assert state['queried_order_id']=='ORD-TEST-001' and state['order_status_result']=='FOUND'
                assert state.get('active_order_confirmation') is None
            elif expected=='clarification':
                assert state['intent']=='UNKNOWN' and 'Précisez' in state['response']
                assert state.get('pending_order') is None and state.get('active_order_confirmation') is None
            elif expected=='stock':
                assert state['intent']=='STOCK_CHECK'
                assert state.get('active_order_confirmation') is None
                assert '20' in state['response']
            else:
                assert state['order_status']=='INVALID_QUANTITY'
                assert state.get('active_order_confirmation') is None
            assert list(database.products.find({}).sort('_id',1))==before
            assert list(database.orders.find({}))==before_orders
            tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize('collision',['owned','foreign','duplicate','unknown'])
def test_collision_and_revocation_survive_checkpoint_reopening(database,checkpoint_uri,collision):
    pid,products,orders,tx,classifier,app=harness(database)
    thread=uuid4().hex;cfg={'configurable':{'thread_id':customer_thread_key(OWNER,thread)}}
    async def scenario():
        classifier.execute.return_value=IntentResult(intent=Intent.ORDER,quantity=3)
        async with open_checkpointer(checkpoint_uri) as saver:
            first=await post(app(saver),thread,'Commande 3 '+SKU)
            assert first.json()['active_order_confirmation']
        if collision in ('owned','foreign'):
            await seed(orders,SKU,OWNER if collision=='owned' else 'different-owner')
        if collision=='duplicate':
            database.products.insert_one({'sku':SKU.lower(),'name':'Autre référence exacte',
                'category':'DRILL','stock':20,'price':1.})
        reference='ABC-123' if collision=='unknown' else SKU
        before=list(database.products.find({}).sort('_id',1));before_orders=list(database.orders.find({}))
        classifier.execute.return_value=IntentResult(intent=Intent.ORDER_STATUS,order_id=reference)
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver);response=await post(api,thread,'Commande '+reference)
            assert response.status_code==200
            result=response.json()
            if collision=='foreign':
                # Other-owner existence is not an order in this namespace.
                assert result['pending_order']['quantity']==1
                assert result['active_order_confirmation']
                assert result['pending_order']['operation_id'] != first.json()['pending_order']['operation_id']
                assert list(database.products.find({}).sort('_id',1))==before
                assert list(database.orders.find({}))==before_orders
                tx.products.reserve_stock_sku.assert_not_called()
                return
            assert result['intent']=='UNKNOWN' and result['pending_order'] is None
            assert result['active_order_confirmation'] is None
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver);restored=(await api.state.stockpilot_workflow.aget_state(cfg)).values
            assert restored['pending_order'] is None and restored['active_order_confirmation'] is None
            after=await post(api,thread,'oui confirme')
            assert after.status_code==200
            assert after.json().get('active_order_confirmation') is None
            assert list(database.products.find({}).sort('_id',1))==before
            assert list(database.orders.find({}))==before_orders
            tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


def test_normal_confirmation_and_replay_after_reference_correction(database,checkpoint_uri):
    pid,products,orders,tx,classifier,app=harness(database)
    thread=uuid4().hex
    async def scenario():
        classifier.execute.return_value=IntentResult(intent=Intent.ORDER_STATUS,order_id=SKU)
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver);prepared=(await post(api,thread,'Commande '+SKU)).json()
            operation=prepared['pending_order']['operation_id']
            confirmed=(await post(api,thread,'oui confirme')).json()
            assert confirmed['order_status']=='CONFIRMED'
            assert confirmed['pending_order']['operation_id']==operation
            order_id=confirmed['created_order_id']
        async with open_checkpointer(checkpoint_uri) as saver:
            replay=(await post(app(saver),thread,'oui')).json()
            assert replay['created_order_id']==order_id
            assert replay['pending_order']['operation_id']==operation
            assert database.orders.count_documents({})==1
            assert database.products.find_one({'_id':pid})['stock']==19
            tx.products.reserve_stock_sku.assert_called_once()
    asyncio.run(scenario())


def test_lookup_exception_keeps_retirement_persisted(database,checkpoint_uri):
    pid,products,orders,tx,classifier,app=harness(database)
    thread=uuid4().hex;cfg={'configurable':{'thread_id':customer_thread_key(OWNER,thread)}}
    async def scenario():
        classifier.execute.return_value=IntentResult(intent=Intent.ORDER,quantity=3)
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver)
            assert (await post(api,thread,'Commande 3 '+SKU)).json()['active_order_confirmation']
            original=orders.find_by_order_id_and_customer_id
            orders.find_by_order_id_and_customer_id=AsyncMock(side_effect=RuntimeError('local lookup failure'))
            failed=await post(api,thread,'Commande '+SKU)
            assert failed.status_code==500
            orders.find_by_order_id_and_customer_id=original
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver)
            restored=(await api.state.stockpilot_workflow.aget_state(cfg)).values
            assert restored['active_order_confirmation'] is None
            response=await post(api,thread,'oui')
            assert response.status_code==200 and response.json()['order_status']=='PRODUCT_IDENTITY_REQUIRED'
            assert database.orders.count_documents({})==0
            assert database.products.find_one({'_id':pid})['stock']==20
            tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize('reference',['ORD-TEST-001','DEMO-DRILL-9000','550e8400-e29b-41d4-a716-446655440000'])
def test_bare_owned_order_is_read_only_even_with_purchase_classification(database,checkpoint_uri,reference):
    pid,products,orders,tx,classifier,app=harness(database)
    async def scenario():
        await seed(orders,reference)
        before=list(database.orders.find({}));before_products=list(database.products.find({}))
        classifier.execute.return_value=IntentResult(intent=Intent.ORDER,quantity=1,product_name=SKU)
        async with open_checkpointer(checkpoint_uri) as saver:
            result=(await post(app(saver),uuid4().hex,'Commande '+reference)).json()
            assert result['intent']=='ORDER_STATUS'
            assert result['queried_order_id']==reference and result['order_status_result']=='FOUND'
            assert result.get('pending_order') is None
            assert result.get('active_order_confirmation') is None
        assert list(database.orders.find({}))==before
        assert list(database.products.find({}))==before_products
        tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize('reference',['550e8400-e29b-41d4-a716-446655440000','MODEL-12-34-56','2','une'])
def test_actual_catalogue_sku_namespace_variants(database,checkpoint_uri,reference):
    pid,products,orders,tx,classifier,app=harness(database)
    database.products.update_one({'_id':pid},{'$set':{'sku':reference}})
    classifier.execute.return_value=IntentResult(intent=Intent.ORDER_STATUS,order_id=reference)
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            result=(await post(app(saver),uuid4().hex,'Commande '+reference)).json()
            if reference in ('2','une'):
                assert result['intent']=='UNKNOWN' and result.get('pending_order') is None
            else:
                assert result['pending_order']['quantity']==1
                assert result['pending_order']['product_sku']==reference
                assert result['pending_order']['product_document_id']==str(pid)
            assert database.orders.count_documents({})==0
            assert database.products.find_one({'_id':pid})['stock']==20
            tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


def test_explicit_status_with_catalogue_collision_never_prepares(database,checkpoint_uri):
    pid,products,orders,tx,classifier,app=harness(database)
    async def scenario():
        await seed(orders,SKU)
        before=list(database.orders.find({}))
        classifier.execute.return_value=IntentResult(intent=Intent.ORDER_STATUS,order_id=SKU)
        async with open_checkpointer(checkpoint_uri) as saver:
            result=(await post(app(saver),uuid4().hex,'Statut de la commande '+SKU)).json()
            assert result['order_status_result']=='FOUND' and result['queried_order_id']==SKU
            assert result.get('pending_order') is None and result.get('active_order_confirmation') is None
            assert list(database.orders.find({}))==before
            assert database.products.find_one({'_id':pid})['stock']==20
            tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


def test_unsupported_confirmation_phrase_disarms_old_preparation(database,checkpoint_uri):
    pid,products,orders,tx,classifier,app=harness(database)
    thread=uuid4().hex
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver)
            classifier.execute.return_value=IntentResult(intent=Intent.ORDER,quantity=3)
            prepared=(await post(api,thread,'Commande 3 '+SKU)).json()
            assert prepared['active_order_confirmation']
            classifier.execute.return_value=IntentResult(intent=Intent.ORDER)
            result=(await post(api,thread,'Confirme la commande')).json()
            assert result['order_status']=='INVALID_QUANTITY'
            assert result['active_order_confirmation'] is None
            assert database.orders.count_documents({})==0
            assert database.products.find_one({'_id':pid})['stock']==20
            tx.products.reserve_stock_sku.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize('normalize',[False,True])
def test_confirmed_recovery_after_unknown_reference_remains_read_only(database,checkpoint_uri,normalize):
    """Characterize the inherited replay contract, not a new purchase approval.

    A confirmed pending operation survives unrelated turns and can be recovered
    by oui. The response identifies that already confirmed order; no reservation
    may be caused by the unresolved new identifier.
    """
    pid,products,orders,tx,classifier,app=harness(database,normalize=normalize)
    thread=uuid4().hex
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as saver:
            api=app(saver)
            classifier.execute.return_value=IntentResult(intent=Intent.ORDER,quantity=1)
            prepared=(await post(api,thread,'Je veux commander '+SKU)).json()
            assert prepared['active_order_confirmation']
            confirmed=(await post(api,thread,'oui')).json()
            order_id=confirmed['created_order_id'];operation=confirmed['pending_order']['operation_id']
            before=list(database.products.find({}));before_orders=list(database.orders.find({}))
            classifier.execute.return_value=IntentResult(intent=Intent.ORDER_STATUS,order_id='ABC-123')
            unknown=(await post(api,thread,'Commande ABC-123')).json()
            if normalize:
                assert unknown['intent']=='UNKNOWN' and 'Précisez' in unknown['response']
            replay=(await post(api,thread,'oui')).json()
            assert replay['created_order_id']==order_id
            assert replay['pending_order']['operation_id']==operation
            assert 'déjà' in replay['response']
            assert list(database.products.find({}))==before
            assert list(database.orders.find({}))==before_orders
            tx.products.reserve_stock_sku.assert_called_once()
    asyncio.run(scenario())
