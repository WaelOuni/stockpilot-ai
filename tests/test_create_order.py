import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from dataclasses import replace
from uuid import UUID

import pytest
from pymongo.errors import DuplicateKeyError

from stockpilot.application.order_idempotency import IdempotencyConflictError
from stockpilot.application.use_cases.create_order import CreateOrderUseCase
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.persistence.mongodb.order_repository import (
    MongoOrderRepository,
)


def test_create_order_generates_id_and_persists_created_order_once() -> None:
    repository = MagicMock()
    repository.create = AsyncMock(side_effect=lambda order: order)
    use_case = CreateOrderUseCase(repository)

    order = asyncio.run(use_case.execute(" Bosch X100 ", quantity=2, operation_id="op-123"))

    UUID(order.order_id)
    assert order.product_name == "Bosch X100"
    assert order.operation_id == "op-123"
    assert order.quantity == 2
    assert order.status == OrderStatus.CREATED
    assert order.created_at.tzinfo == timezone.utc
    repository.create.assert_awaited_once()
    assert repository.create.await_args.args[0] == order


def test_create_order_rejects_invalid_product_and_quantity() -> None:
    repository = MagicMock()
    repository.create = AsyncMock()
    use_case = CreateOrderUseCase(repository)

    for product_name, quantity in [("", 1), ("Bosch X100", 0)]:
        with pytest.raises(ValueError):
            asyncio.run(use_case.execute(product_name, quantity, operation_id="op-123"))

    repository.create.assert_not_awaited()


def test_mongo_order_repository_serializes_order_document() -> None:
    collection = MagicMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    repository = MongoOrderRepository(database)
    order = Order(
        order_id="order-123",
        operation_id="op-123",
        product_name="Bosch X100",
        quantity=1,
        status=OrderStatus.CREATED,
        created_at=datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc),
    )

    result = asyncio.run(repository.create(order))

    assert result == order
    collection.insert_one.assert_called_once_with(
        {
            "order_id": "order-123",
            "operation_id": "op-123",
            "product_name": "Bosch X100",
            "quantity": 1,
            "status": "CREATED",
            "created_at": "2026-10-09T20:00:00+00:00",
        }
    )


def make_order() -> Order:
    return Order(
        order_id="order-123",
        operation_id="op-123",
        product_name="Bosch X100",
        quantity=3,
        status=OrderStatus.CREATED,
        created_at=datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc),
    )


def make_repository():
    collection = MagicMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    return MongoOrderRepository(database), collection


def order_document(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "operation_id": order.operation_id,
        "product_name": order.product_name,
        "quantity": order.quantity,
        "status": order.status.value,
        "created_at": order.created_at.isoformat(),
    }


def test_unique_partial_index_does_not_include_legacy_orders() -> None:
    repository, collection = make_repository()

    repository.initialize()

    collection.create_index.assert_called_once_with(
        [("operation_id", 1)],
        name="orders_operation_id_unique",
        unique=True,
        partialFilterExpression={"operation_id": {"$type": "string"}},
    )
    collection.delete_many.assert_not_called()


def test_lookup_deserializes_order_and_reports_absence() -> None:
    repository, collection = make_repository()
    order = make_order()
    collection.find_one.side_effect = [order_document(order), None]

    assert asyncio.run(repository.find_by_operation_id("op-123")) == order
    collection.find_one.assert_called_with({"operation_id": "op-123"})
    assert asyncio.run(repository.find_by_operation_id("missing")) is None


def test_replay_returns_exact_original_with_one_effective_insert() -> None:
    repository, collection = make_repository()
    documents = {}

    def insert(document):
        key = document["operation_id"]
        if key in documents:
            raise DuplicateKeyError("duplicate operation")
        documents[key] = document

    collection.insert_one.side_effect = insert
    collection.find_one.side_effect = lambda query: documents.get(query["operation_id"])
    first = make_order()
    replay = replace(first, order_id="new-id", created_at=datetime.now(timezone.utc))

    assert asyncio.run(repository.create(first)) == first
    assert asyncio.run(repository.create(replay)) == first
    assert len(documents) == 1


@pytest.mark.parametrize("changes", [{"quantity": 5}, {"product_name": "Makita D200"}])
def test_duplicate_operation_rejects_different_payload(changes) -> None:
    repository, collection = make_repository()
    first = make_order()
    collection.insert_one.side_effect = DuplicateKeyError("duplicate operation")
    collection.find_one.return_value = order_document(first)

    with pytest.raises(IdempotencyConflictError):
        asyncio.run(repository.create(replace(first, **changes)))


def test_duplicate_without_matching_operation_is_not_swallowed() -> None:
    repository, collection = make_repository()
    collection.insert_one.side_effect = DuplicateKeyError("other unique index")
    collection.find_one.return_value = None

    with pytest.raises(DuplicateKeyError):
        asyncio.run(repository.create(make_order()))


def test_use_case_lookup_checks_business_payload() -> None:
    repository = MagicMock()
    repository.find_by_operation_id = AsyncMock(return_value=make_order())
    use_case = CreateOrderUseCase(repository)

    assert asyncio.run(use_case.find_existing("op-123", "Bosch X100", 3)) == make_order()
    with pytest.raises(IdempotencyConflictError):
        asyncio.run(use_case.find_existing("op-123", "Bosch X100", 5))


def test_create_rejects_empty_operation_id_without_inserting() -> None:
    repository = MagicMock()
    repository.create = AsyncMock()
    use_case = CreateOrderUseCase(repository)

    with pytest.raises(ValueError, match="operation"):
        asyncio.run(use_case.execute("Bosch X100", 3, operation_id=" "))

    repository.create.assert_not_awaited()
