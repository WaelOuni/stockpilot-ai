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
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from customer_auth import customer_headers
from customer_auth import configure_customer_auth
from test_mongo_transaction_integration import database
from test_postgres_checkpoint_integration import checkpoint_uri
from test_v1233_reference_checkpoint_integration import harness, post, OWNER, SKU
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.resource_errors import ModelInputLimitError, ModelTimeoutError, ModelUnavailableError
from stockpilot.domain.enums.intent import Intent
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer
from stockpilot.presentation.api.input_protection import InputProtectionMiddleware, safe_model_resource_error, safe_validation_error
from stockpilot.presentation.api.input_protection import require_bounded_path_identifiers
from stockpilot.presentation.api.routes.assistant import customer_thread_key

pytestmark = pytest.mark.integration


def test_largest_imported_product_identity_is_retrievable_with_tight_non_sku_limits(database):
    from stockpilot.application.input_limits import MAX_PRODUCT_TEXT_CHARS
    from stockpilot.application.use_cases.catalog_products import GetProductUseCase
    from stockpilot.application.use_cases.product_csv_import import PreviewProductCsvImportUseCase, ExecuteProductCsvImportUseCase
    from stockpilot.infrastructure.persistence.mongodb.product_import import MongoProductCsvImport, prepare_import_index
    from stockpilot.infrastructure.persistence.mongodb.product_indexes import prepare_unique_sku_index
    from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
    from stockpilot.presentation.api.auth import require_customer_identity
    from stockpilot.presentation.api.routes.products import router
    from stockpilot.presentation.api.schemas.backoffice_product import ProductMetadataRequest

    prepare_unique_sku_index(database.products)
    prepare_import_index(database.product_imports)
    sku, name = "é" * MAX_PRODUCT_TEXT_CHARS, "N" * MAX_PRODUCT_TEXT_CHARS
    content = f"sku,name,category,price\n{sku},{name},DRILL,1\n".encode()
    preview = PreviewProductCsvImportUseCase(MongoProductCsvImport(database))
    approved = preview.execute(content, "synthetic-session")
    assert approved.invalid == 0 and approved.valid == 1 and approved.binding
    result = ExecuteProductCsvImportUseCase(preview).execute(
        content, "synthetic-session", str(uuid4()), approved.binding, True,
    )
    assert result.created == 1
    api = FastAPI(dependencies=[Depends(require_customer_identity), Depends(require_bounded_path_identifiers)])
    configure_customer_auth(None, api)
    # Tight order/operation limits never make valid CSV product identities
    # inaccessible. Product text has its separate shared compatibility ceiling.
    api.state.settings = api.state.settings.model_copy(update={"api_identifier_max_chars": 8})
    api.include_router(router)
    repository = MongoProductRepository(database)
    api.state.get_product_use_case = GetProductUseCase(repository)
    response = TestClient(api).get("/products/" + sku, headers=customer_headers())
    assert response.status_code == 200 and response.json()["sku"] == sku and response.json()["product_name"] == name
    assert response.json()["stock"] == 0
    changes = ProductMetadataRequest(name=name).model_dump(exclude_unset=True)
    updated = asyncio.run(repository.update_metadata(sku, changes))
    assert updated.sku == sku and updated.name == name and updated.stock == 0


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
