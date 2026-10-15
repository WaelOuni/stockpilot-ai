"""Real HTTP adapter, ASGI endpoint, MongoDB and reconnecting PostgreSQL.

Only the local HTTP transport for the test's adapter is fault-injected; no
Ollama, DEMO process or shared network is contacted or altered.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from customer_auth import configure_customer_auth, customer_headers
from test_mongo_transaction_integration import database
from test_postgres_checkpoint_integration import checkpoint_uri
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer
from stockpilot.presentation.api.routes.assistant import router, customer_thread_key

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('timeout,confirmation', [(True,'oui'),(True,'oui confirme'),(False,'oui')])
def test_provider_readtimeout_revocation_survives_new_http_graph_and_pg_connection(
    database, checkpoint_uri, monkeypatch, timeout, confirmation,
):
    native_client = httpx.AsyncClient
    requests = []
    def provider(request):
        requests.append(request)
        assert request.url.host == 'v1232-llm.invalid'
        if len(requests) > 1:
            raise httpx.ReadTimeout('Controlled local classification failure', request=request)
        return httpx.Response(200, json={'response':json.dumps({
            'intent':'ORDER','raw_product':'V1232-DRILL-0001',
            'product_name':None,'quantity':3,
        })})
    def local_client(**kwargs):
        assert kwargs['timeout'] == 60.0  # production timeout is not changed
        return native_client(**kwargs, transport=httpx.MockTransport(provider))
    monkeypatch.setattr('stockpilot.infrastructure.llm.ollama_adapter.httpx.AsyncClient',local_client)
    settings = Settings(_env_file=None, ollama_base_url='http://v1232-llm.invalid')
    classifier=ClassifyIntentUseCase(OllamaAdapter(settings))
    repo=MongoProductRepository(database)
    transaction=MongoOrderTransaction(database)
    transaction.products.reserve_stock_sku=Mock(wraps=transaction.products.reserve_stock_sku)
    transaction.execute=AsyncMock(wraps=transaction.execute)
    database.products.insert_one({'sku':'V1232-DRILL-0001','name':'Perceuse Test V1232 18V',
        'category':'DRILL','price':1.,'stock':10})
    before=list(database.products.find({}).sort('_id',1))
    owner='v1232-synthetic';wire_thread=uuid4().hex
    config={'configurable':{'thread_id':customer_thread_key(owner,wire_thread)}}
    def app(saver):
        api=FastAPI();api.include_router(router);configure_customer_auth(None,api)
        api.state.stockpilot_workflow=build_graph(
            classifier,CheckStockUseCase(repo),SimpleNamespace(execute=AsyncMock()),
            PrepareOrderUseCase(CheckStockUseCase(repo),ResolveProductUseCase(repo)),
            FinalizeOrderUseCase(transaction),checkpointer=saver,
            get_order_status=GetOrderStatusUseCase(MongoOrderRepository(database)),
        )
        return api
    async def post(api,message):
        async with native_client(transport=httpx.ASGITransport(app=api,raise_app_exceptions=False),
                                 base_url='http://local-test') as client:
            return await client.post('/assistant',json={'thread_id':wire_thread,'message':message},
                                     headers=customer_headers(owner))
    async def scenario():
        async with open_checkpointer(checkpoint_uri) as first:
            api=app(first)
            prepared=await post(api,'Commande 3 V1232-DRILL-0001')
            assert prepared.status_code==200
            pending=prepared.json()['pending_order']
            assert prepared.json()['active_order_confirmation']['operation_id']==pending['operation_id']
            assert pending['quantity']==3
            assert pending['product_document_id']==str(database.products.find_one({'sku':'V1232-DRILL-0001'})['_id'])
        assert first.conn.closed
        if timeout:
            async with open_checkpointer(checkpoint_uri) as second:
                api=app(second)
                failed=await post(api,'Je veux commander 2 V1232-DRILL-0001')
                assert failed.status_code==500  # provider failure remains a server error
                assert len(requests)==2
                restored=(await api.state.stockpilot_workflow.aget_state(config)).values
                assert restored['active_order_confirmation'] is None
                assert restored['pending_order']['operation_id']==pending['operation_id']
                assert not database.orders.count_documents({})
                assert list(database.products.find({}).sort('_id',1))==before
            assert second.conn.closed
        async with open_checkpointer(checkpoint_uri) as third:
            api=app(third)
            restored=(await api.state.stockpilot_workflow.aget_state(config)).values
            assert restored['pending_order']['operation_id']==pending['operation_id']
            if timeout:
                assert restored['active_order_confirmation'] is None
            else:
                assert restored['active_order_confirmation']['operation_id']==pending['operation_id']
            confirmed=await post(api,confirmation)
            assert confirmed.status_code==200
            result=confirmed.json()
            assert result['pending_order']['operation_id']==pending['operation_id']
            if timeout:
                assert result['order_status']=='PRODUCT_IDENTITY_REQUIRED'
                assert result['created_order_id'] is None
                assert result['active_order_confirmation'] is None
                assert database.orders.count_documents({})==0
                assert list(database.products.find({}).sort('_id',1))==before
                assert transaction.execute.await_args.kwargs=={'replay_only':True}
                transaction.products.reserve_stock_sku.assert_not_called()
                assert len(requests)==2  # oui does not contact the failed provider again
            else:
                assert result['order_status']=='CONFIRMED'
                assert database.orders.count_documents({})==1
                assert database.products.find_one({'sku':'V1232-DRILL-0001'})['stock']==7
                transaction.products.reserve_stock_sku.assert_called_once()
                replay=await post(api,'oui confirme')
                assert replay.status_code==200 and replay.json()['created_order_id']==result['created_order_id']
                assert database.orders.count_documents({})==1
                transaction.products.reserve_stock_sku.assert_called_once()
                assert len(requests)==1
        assert third.conn.closed
    asyncio.run(scenario())
