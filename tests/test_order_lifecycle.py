import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.use_cases.update_order_status import (
    OrderStatusConflictError,
    UpdateOrderStatusUseCase,
)
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.services.order_lifecycle import (
    InvalidOrderStatusTransitionError,
    validate_order_status_transition,
)
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository


ALLOWED = {
    (OrderStatus.CREATED, OrderStatus.PROCESSING),
    (OrderStatus.PROCESSING, OrderStatus.SHIPPED),
    (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
}


def make_order(status=OrderStatus.CREATED):
    return Order(
        order_id="order-1", operation_id="op-1", product_name="Bosch X100",
        quantity=2, status=status, created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("current", list(OrderStatus))
@pytest.mark.parametrize("target", list(OrderStatus))
def test_all_transitions_and_identical_status(current, target):
    order = make_order(current)
    updated = replace(order, status=target)
    repo = SimpleNamespace(
        find_by_order_id=AsyncMock(return_value=order),
        update_status=AsyncMock(return_value=updated),
    )
    use_case = UpdateOrderStatusUseCase(repo)
    if current == target:
        assert asyncio.run(use_case.execute(order.order_id, target)) is order
        repo.update_status.assert_not_awaited()
    elif (current, target) in ALLOWED:
        assert asyncio.run(use_case.execute(order.order_id, target)) == updated
        repo.update_status.assert_awaited_once_with(
            order.order_id, expected_status=current, status=target,
        )
    else:
        with pytest.raises(InvalidOrderStatusTransitionError):
            asyncio.run(use_case.execute(order.order_id, target))
        repo.update_status.assert_not_awaited()


def test_missing_order_is_not_updated():
    repo = SimpleNamespace(find_by_order_id=AsyncMock(return_value=None), update_status=AsyncMock())
    assert asyncio.run(UpdateOrderStatusUseCase(repo).execute("missing", OrderStatus.PROCESSING)) is None
    repo.update_status.assert_not_awaited()


def test_concurrent_change_is_explicit_conflict_without_retry():
    repo = SimpleNamespace(
        find_by_order_id=AsyncMock(return_value=make_order()),
        update_status=AsyncMock(return_value=None),
    )
    with pytest.raises(OrderStatusConflictError):
        asyncio.run(UpdateOrderStatusUseCase(repo).execute("order-1", OrderStatus.PROCESSING))
    assert repo.update_status.await_count == 1


@pytest.mark.parametrize("phase", ["read", "update"])
def test_database_errors_propagate(phase):
    error = PyMongoError("database unavailable")
    repo = SimpleNamespace(
        find_by_order_id=AsyncMock(return_value=make_order()),
        update_status=AsyncMock(),
    )
    getattr(repo, "find_by_order_id" if phase == "read" else "update_status").side_effect = error
    with pytest.raises(PyMongoError) as caught:
        asyncio.run(UpdateOrderStatusUseCase(repo).execute("order-1", OrderStatus.PROCESSING))
    assert caught.value is error


@pytest.mark.parametrize("identifier", ["", " "])
def test_empty_id_rejected_before_database(identifier):
    repo = SimpleNamespace(find_by_order_id=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(UpdateOrderStatusUseCase(repo).execute(identifier, OrderStatus.PROCESSING))
    repo.find_by_order_id.assert_not_awaited()


def test_plain_strings_rejected_at_technical_use_case_boundary():
    repo = SimpleNamespace(find_by_order_id=AsyncMock())
    with pytest.raises(TypeError):
        asyncio.run(UpdateOrderStatusUseCase(repo).execute("order-1", "PROCESSING"))
    repo.find_by_order_id.assert_not_awaited()
    with pytest.raises(TypeError):
        validate_order_status_transition("CREATED", OrderStatus.PROCESSING)


def make_repository():
    collection = MagicMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    return MongoOrderRepository(database), collection


def document(order):
    return {
        "order_id": order.order_id, "operation_id": order.operation_id,
        "product_name": order.product_name, "quantity": order.quantity,
        "status": order.status.value, "created_at": order.created_at.isoformat(),
        **({"customer_id": order.customer_id} if order.customer_id is not None else {}),
    }


@pytest.mark.parametrize("current,target", list(ALLOWED))
def test_mongo_atomic_update_serialization(current, target):
    repo, collection = make_repository()
    expected = make_order(target)
    collection.find_one_and_update.return_value = document(expected)
    assert asyncio.run(repo.update_status(
        expected.order_id, expected_status=current, status=target,
    )) == expected
    collection.find_one_and_update.assert_called_once_with(
        {"order_id": expected.order_id, "status": current.value},
        {"$set": {"status": target.value}}, return_document=ReturnDocument.AFTER,
    )
    collection.insert_one.assert_not_called()
    collection.delete_one.assert_not_called()


def test_mongo_no_match_is_none_and_write_errors_propagate():
    repo, collection = make_repository()
    collection.find_one_and_update.return_value = None
    assert asyncio.run(repo.update_status(
        "missing", expected_status=OrderStatus.CREATED, status=OrderStatus.PROCESSING,
    )) is None
    collection.find_one_and_update.side_effect = PyMongoError("failed")
    with pytest.raises(PyMongoError):
        asyncio.run(repo.update_status(
            "order-1", expected_status=OrderStatus.CREATED, status=OrderStatus.PROCESSING,
        ))


def test_full_lifecycle_mongo_truth_status_lookup_and_replay():
    repo, collection = make_repository()
    original = replace(make_order(), customer_id="customer-a")
    stored = document(original)
    del stored["operation_id"]  # Older CREATED documents remain readable.

    def find(query):
        return dict(stored) if all(stored.get(k) == v for k, v in query.items()) else None

    def update(query, changes, **kwargs):
        if find(query) is None:
            return None
        stored.update(changes["$set"])
        return dict(stored)

    collection.find_one.side_effect = find
    collection.find_one_and_update.side_effect = update
    updater = UpdateOrderStatusUseCase(repo)
    reader = GetOrderStatusUseCase(repo)
    for status in (
        OrderStatus.CREATED, OrderStatus.PROCESSING,
        OrderStatus.SHIPPED, OrderStatus.DELIVERED,
    ):
        order = asyncio.run(updater.execute(original.order_id, status))
        assert order.status is status
        assert order.operation_id == ""
        result = asyncio.run(reader.execute(original.order_id, customer_id="customer-a"))
        assert result.status is status
        assert result.response == f"Commande order-1 : statut {status.value}."
        assert result.result == "FOUND"
    assert collection.find_one_and_update.call_count == 3
    expected_document = document(replace(original, status=OrderStatus.DELIVERED))
    del expected_document["operation_id"]
    assert stored == expected_document
    collection.insert_one.assert_not_called()


def test_creation_replay_returns_progressed_order_without_resetting_status():
    repo, collection = make_repository()
    original = make_order()
    progressed = replace(original, status=OrderStatus.SHIPPED)
    collection.insert_one.side_effect = DuplicateKeyError("same operation")
    collection.find_one.return_value = document(progressed)
    assert asyncio.run(repo.create(replace(original, order_id="new-id"))) == progressed
    collection.find_one_and_update.assert_not_called()
