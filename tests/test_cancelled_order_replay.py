import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.workflows.nodes import (
    create_order_confirmation_node, create_order_node, route_after_confirmation,
)
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction

from test_order_product_identity import Database, product


def run(awaitable):
    return asyncio.run(awaitable)


def prepare():
    stock = AsyncMock()
    stock.execute.return_value = Product(
        sku="bosch", name="Bosch X100", category=ProductCategory.DRILL,
        price=99, stock=10,
    )
    node = create_order_node(PrepareOrderUseCase(stock))
    prepared = run(node({"product_name": "Bosch X100", "quantity": 2, "customer_id": "owner"}))
    assert prepared["order_status"] == "PENDING_CONFIRMATION"
    # Recreate a pre-hotfix checkpoint; SKU-backed replays are covered in Mongo tests.
    prepared["pending_order"].pop("product_sku")
    return {**prepared, "customer_id": "owner", "message": "Oui, je confirme"}


@pytest.mark.parametrize("status", list(OrderStatus))
def test_confirmation_replay_preserves_persisted_order_and_workflow_contract(status):
    target = product("Bosch X100", 10)
    similar = product("Bosch X100 Pro", 0)
    database = Database([similar, target])
    node = create_order_confirmation_node(FinalizeOrderUseCase(MongoOrderTransaction(database)))
    pending = prepare()
    run(FinalizeOrderUseCase(MongoOrderTransaction(database)).execute(
        "Bosch X100", 2, pending["pending_order"]["operation_id"], "owner",
    ))

    initial = run(node(pending))
    order_id = initial["created_order_id"]
    assert initial["order_status"] == initial["pending_order"]["status"] == "CONFIRMED"
    assert initial["last_created_order_id"] == order_id
    assert initial["confirmation_handled"] is True
    assert initial["response"] == f"Commande {order_id} créée pour 2 Bosch X100."
    assert database.stocks() == {similar["_id"]: 0, target["_id"]: 8}
    persisted = database.state["orders"][0]
    assert persisted["customer_id"] == "owner"
    assert persisted["reserved_product_id"] == target["_id"]
    assert persisted["operation_id"] == pending["pending_order"]["operation_id"]
    assert [event["status"] for event in persisted["events"]] == ["CREATED"]

    repository = MongoOrderRepository(database)
    if status == OrderStatus.CANCELLED:
        run(repository.cancel(order_id, expected_status=OrderStatus.CREATED, customer_id="owner"))
    else:
        current = OrderStatus.CREATED
        for following in (OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED):
            if current == status:
                break
            run(repository.update_status(order_id, expected_status=current, status=following))
            current = following

    before = deepcopy(database.state)
    writes = deepcopy(database.writes)
    reads = len(database.reads)
    # Simulate a restored pre-confirmation checkpoint with the same operation.
    replay = run(node(pending))
    expected = "CANCELLED" if status == OrderStatus.CANCELLED else "CONFIRMED"
    assert replay["order_status"] == replay["pending_order"]["status"] == expected
    assert replay["confirmation_handled"] is True
    assert replay["created_order_id"] == replay["last_created_order_id"] == order_id
    assert replay["pending_order"]["operation_id"] == pending["pending_order"]["operation_id"]
    assert replay["pending_order"]["customer_id"] == "owner"
    assert route_after_confirmation(replay) == "end"
    assert database.state == before
    assert database.writes == writes
    assert database.state["orders"][0]["status"] == status.value
    assert database.stocks() == {
        similar["_id"]: 0, target["_id"]: 10 if status == OrderStatus.CANCELLED else 8,
    }
    assert database.reads[reads:] == [
        ("orders", {"operation_id": {"$eq": pending["pending_order"]["operation_id"],
                                    "$type": "string"}}, None),
    ]
    if status == OrderStatus.CANCELLED:
        assert "existe déjà" in replay["response"]
        assert "elle est annulée" in replay["response"]
        assert not replay["response"].startswith("Commande ")
    else:
        assert replay["response"] == initial["response"]

    # A completed confirmation now returns an explicit replay response, but
    # still performs only the validated existing-order read, never a reserve.
    terminal = run(node({**pending, **replay}))
    if status == OrderStatus.CANCELLED:
        assert terminal == {"confirmation_handled": False}
    else:
        assert terminal["confirmation_handled"] is True
        assert terminal["created_order_id"] == order_id
        assert terminal["pending_order"]["operation_id"] == pending["pending_order"]["operation_id"]
        assert "déjà confirmée" in terminal["response"]
    assert database.state == before
    assert database.writes == writes
