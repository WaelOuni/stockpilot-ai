import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.check_stock import StockResult
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer


pytestmark = pytest.mark.integration


@pytest.fixture
def checkpoint_uri():
    uri = os.environ.get("POSTGRES_TEST_URI", Settings().postgres_url)
    schema = "stockpilot_checkpoint_test_" + uuid4().hex
    with psycopg.connect(uri, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield make_conninfo(uri, options=f"-c search_path={schema}")
        finally:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def test_order_state_survives_new_graph_and_connection(checkpoint_uri):
    async def scenario():
        order_intent = IntentResult(
            intent=Intent.ORDER,
            raw_product="2 Bosch X100",
            product_name="Bosch X100",
            category=ProductCategory.DRILL,
            quantity=2,
            budget=150.0,
            usage="renovation",
        )
        classifier = SimpleNamespace(execute=AsyncMock(
            side_effect=lambda message: (
                order_intent if message.startswith("Commande-moi")
                else IntentResult(intent=Intent.ORDER_STATUS) if message == "Où en est ma commande ?"
                else IntentResult(intent=Intent.UNKNOWN)
            )
        ))
        check_stock = SimpleNamespace(execute=AsyncMock(return_value=StockResult(
            sku="BOSCH-X100", name="Bosch X100", stock=11, available=True, document_id="507f1f77bcf86cd799439011"
        )))
        recommendation = SimpleNamespace(execute=AsyncMock())

        orders = {}
        async def finalize(product_name, quantity, operation_id, customer_id=None, *, product_sku=None, product_document_id=None):
            order = Order(
                order_id="created-order",
                operation_id=operation_id,
                product_name=product_name,
                quantity=quantity,
                status=OrderStatus.CREATED,
                created_at=datetime.now(timezone.utc),
                customer_id=customer_id,
                product_sku=product_sku,
                product_document_id=product_document_id,
            )
            orders[order.order_id] = order
            return order

        finalizer = SimpleNamespace(execute=AsyncMock(side_effect=finalize))
        repository = SimpleNamespace(find_by_order_id=AsyncMock(side_effect=orders.get))

        def graph(saver):
            return build_graph(
                classifier, check_stock, recommendation,
                PrepareOrderUseCase(check_stock), finalizer, checkpointer=saver,
                get_order_status=GetOrderStatusUseCase(repository),
            )

        config = {"configurable": {"thread_id": "persistent-order-1"}}
        async with open_checkpointer(checkpoint_uri) as first:
            graph_a = graph(first)
            prepared = await graph_a.ainvoke(
                {"message": "Commande-moi 2 Bosch X100", "customer_id": "customer-a"}, config=config
            )
            UUID(prepared["pending_order"]["operation_id"])
            assert prepared["order_status"] == "PENDING_CONFIRMATION"
            await graph_a.aupdate_state(config, {"created_order_id": None})
        assert first.conn.closed
        del graph_a

        async with open_checkpointer(checkpoint_uri) as second:
            graph_b = graph(second)
            restored = (await graph_b.aget_state(config)).values
            for key in (
                "intent", "raw_product", "product_name", "category", "budget",
                "usage", "quantity", "pending_order", "order_status",
                "confirmation_handled",
            ):
                assert restored[key] == prepared[key]
            assert restored["created_order_id"] is None
            assert restored["pending_order"]["product_sku"] == "BOSCH-X100"
            isolated = await graph_b.ainvoke(
                {"message": "Oui, je confirme"},
                config={"configurable": {"thread_id": "unrelated-thread"}},
            )
            assert isolated.get("pending_order") is None
            finalizer.execute.assert_not_awaited()
            confirmed = await graph_b.ainvoke(
                {"message": "Oui, je confirme"}, config=config
            )
            finalizer.execute.assert_awaited_once_with(
                "Bosch X100", 2,
                operation_id=prepared["pending_order"]["operation_id"],
                customer_id="customer-a",
                product_sku="BOSCH-X100",
                product_document_id="507f1f77bcf86cd799439011",
            )
            assert confirmed["order_status"] == "CONFIRMED"
            assert confirmed["created_order_id"] == "created-order"
            assert confirmed["last_created_order_id"] == "created-order"
            assert confirmed["pending_order"]["operation_id"] == prepared["pending_order"]["operation_id"]
        assert second.conn.closed

        async with open_checkpointer(checkpoint_uri) as third:
            graph_c = graph(third)
            queried = await graph_c.ainvoke({"message": "Où en est ma commande ?"}, config=config)
            assert queried["last_created_order_id"] == "created-order"
            assert queried["queried_order_id"] == "created-order"
            assert queried["queried_order_status"] == "CREATED"
            assert queried["order_status"] == "CONFIRMED"
            repository.find_by_order_id.assert_awaited_once_with("created-order")
            finalizer.execute.assert_awaited_once()
        assert third.conn.closed

    asyncio.run(scenario())


def test_old_name_only_checkpoint_survives_reconnect_without_stock_mutation(checkpoint_uri):
    from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase

    async def scenario():
        transaction = SimpleNamespace(execute=AsyncMock(return_value=None))
        def graph(saver):
            dependency = SimpleNamespace(execute=AsyncMock())
            return build_graph(dependency, dependency, dependency, dependency,
                               FinalizeOrderUseCase(transaction), checkpointer=saver,
                               get_order_status=dependency)
        config = {"configurable": {"thread_id": "legacy-pending"}}
        pending = {"operation_id": "old-operation", "product_name": "Bosch X100",
                   "quantity": 1, "status": "PENDING_CONFIRMATION", "customer_id": "alice"}
        async with open_checkpointer(checkpoint_uri) as first:
            await graph(first).aupdate_state(config, {
                "message": "Commander Bosch X100", "customer_id": "alice",
                "pending_order": pending, "order_status": "PENDING_CONFIRMATION",
            })
        async with open_checkpointer(checkpoint_uri) as second:
            restored = graph(second)
            assert (await restored.aget_state(config)).values["pending_order"] == pending
            result = await restored.ainvoke({"message": "oui"}, config)
            assert result["order_status"] == "PRODUCT_IDENTITY_REQUIRED"
            assert transaction.execute.await_args.kwargs == {"replay_only": True}
            assert transaction.execute.await_args.args[0].product_sku is None
    asyncio.run(scenario())
