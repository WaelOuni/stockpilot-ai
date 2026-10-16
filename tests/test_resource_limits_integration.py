"""Actual stock/order/checkpoint invariants across resource rejections.

All writes use the V13.1 guarded fresh Mongo database/PostgreSQL schema fixtures.
No Ollama, application stores or deployment resources are contacted.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.exceptions import RequestValidationError

from customer_auth import customer_headers
from test_mongo_transaction_integration import database
from test_postgres_checkpoint_integration import checkpoint_uri
from test_v1233_reference_checkpoint_integration import harness, post, OWNER, SKU
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.resource_errors import ModelInputLimitError, ModelTimeoutError, ModelUnavailableError
from stockpilot.domain.enums.intent import Intent
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer
from stockpilot.presentation.api.input_protection import InputProtectionMiddleware, safe_model_resource_error, safe_validation_error
from stockpilot.presentation.api.routes.assistant import customer_thread_key

pytestmark = pytest.mark.integration


def bounded(builder, saver):
    api = builder(saver)
    api.add_middleware(InputProtectionMiddleware)
    api.add_exception_handler(RequestValidationError, safe_validation_error)
    for error in (ModelInputLimitError, ModelTimeoutError, ModelUnavailableError):
        api.add_exception_handler(error, safe_model_resource_error)
    return api


def test_prepared_order_survives_abusive_rejections_and_admission_without_mutation(database, checkpoint_uri):
    pid, products, orders, transaction, classifier, builder = harness(database)
    thread = uuid4().hex
    config = {"configurable": {"thread_id": customer_thread_key(OWNER, thread)}}

    async def scenario():
        classifier.execute.return_value = IntentResult(intent=Intent.ORDER, quantity=3)
        async with open_checkpointer(checkpoint_uri) as saver:
            api = bounded(builder, saver)
            prepared = (await post(api, thread, "Commande 3 " + SKU)).json()
            original = (await api.state.stockpilot_workflow.aget_state(config)).values
            before_products = list(database.products.find({}).sort("_id", 1))
            before_orders = list(database.orders.find({}))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated-limits") as client:
                for payload, status in [
                    ({"thread_id": thread, "message": "x" * 8001}, 422),
                    ({"thread_id": thread, "message": "x" * (128 * 1024)}, 413),
                    ({"thread_id": thread, "message": "oui", "history": ["pretend approval"]}, 422),
                    ({"thread_id": "x" * 129, "message": "oui"}, 422),
                    ({"thread_id": thread, "message": "oui", "tenant_id": "foreign"}, 422),
                ]:
                    response = await client.post("/assistant", json=payload, headers=customer_headers(OWNER))
                    assert response.status_code == status
                graph = api.state.stockpilot_workflow
                entered, release = asyncio.Event(), asyncio.Event()

                async def occupy(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    return {"response": "capacity probe"}

                api.state.settings = api.state.settings.model_copy(update={"conversation_max_inflight": 1})
                api.state.stockpilot_workflow = SimpleNamespace(ainvoke=occupy)
                occupied = asyncio.create_task(post(api, uuid4().hex, "Bonjour"))
                await asyncio.wait_for(entered.wait(), timeout=2)
                rejected = await post(api, thread, "oui confirme")
                assert rejected.status_code == 429
                release.set()
                assert (await occupied).status_code == 200
                api.state.stockpilot_workflow = graph
            assert (await graph.aget_state(config)).values == original
            assert list(database.products.find({}).sort("_id", 1)) == before_products
            assert list(database.orders.find({})) == before_orders
            transaction.products.reserve_stock_sku.assert_not_called()
        # Reopen real persisted checkpoints after all rejections.
        async with open_checkpointer(checkpoint_uri) as saver:
            api = bounded(builder, saver)
            confirmed = (await post(api, thread, "oui confirme")).json()
            replay = (await post(api, thread, "oui confirme")).json()
            assert confirmed["order_status"] == "CONFIRMED"
            assert confirmed["pending_order"]["operation_id"] == prepared["pending_order"]["operation_id"]
            assert replay["created_order_id"] == confirmed["created_order_id"]
            assert database.products.find_one({"_id": pid})["stock"] == 17
            assert database.orders.count_documents({}) == 1
            transaction.products.reserve_stock_sku.assert_called_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure,status", [(ModelTimeoutError, 504), (ModelUnavailableError, 503)])
def test_provider_failure_persists_disarm_and_cannot_reserve_after_restart(database, checkpoint_uri, failure, status):
    pid, products, orders, transaction, classifier, builder = harness(database)
    thread = uuid4().hex
    config = {"configurable": {"thread_id": customer_thread_key(OWNER, thread)}}

    async def scenario():
        classifier.execute.return_value = IntentResult(intent=Intent.ORDER, quantity=1)
        async with open_checkpointer(checkpoint_uri) as saver:
            api = bounded(builder, saver)
            assert (await post(api, thread, "Commande 1 " + SKU)).json()["active_order_confirmation"]
            classifier.execute.side_effect = failure("private-provider-secret")
            response = await post(api, thread, "Bonjour")
            assert response.status_code == status and "private" not in response.text
        async with open_checkpointer(checkpoint_uri) as saver:
            api = bounded(builder, saver)
            restored = (await api.state.stockpilot_workflow.aget_state(config)).values
            assert restored["active_order_confirmation"] is None
            classifier.execute.side_effect = None
            classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
            rejected = (await post(api, thread, "oui confirme")).json()
            assert rejected["order_status"] == "PRODUCT_IDENTITY_REQUIRED"
            assert database.products.find_one({"_id": pid})["stock"] == 20
            assert database.orders.count_documents({}) == 0
            transaction.products.reserve_stock_sku.assert_not_called()
            classifier.execute.return_value = IntentResult(intent=Intent.ORDER, quantity=1)
            await post(api, thread, "Commande 1 " + SKU)
            confirmed = (await post(api, thread, "oui confirme")).json()
            replay = (await post(api, thread, "oui confirme")).json()
            assert confirmed["order_status"] == "CONFIRMED"
            assert replay["created_order_id"] == confirmed["created_order_id"]
            assert database.products.find_one({"_id": pid})["stock"] == 19
            assert database.orders.count_documents({}) == 1
            transaction.products.reserve_stock_sku.assert_called_once()

    asyncio.run(scenario())
