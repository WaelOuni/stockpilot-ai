import asyncio
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from threading import Barrier, local
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

from stockpilot.application.order_transaction_errors import OrderTransactionFailedError
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.domain.entities.order import Order
from stockpilot.domain.entities.order_event import OrderEvent, OrderEventSource
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
from stockpilot.application.use_cases.adjust_product_stock import (
    AdjustProductStockUseCase, InsufficientStockError, StockAdjustmentConflictError,
    StockAdjustmentTransactionFailedError,
)
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.stock_movement_indexes import (
    prepare_stock_movement_indexes,
)


pytestmark = pytest.mark.integration


def validate_test_database(database, name):
    assert re.fullmatch(r"stockpilot_transaction_test_[0-9a-f]{32}", name)
    assert name not in {"stockpilot", "stockpilot_e2e"}
    assert database.name == name


def adjust(database, sku, delta, operation_id=None, reason="integration recount"):
    return asyncio.run(AdjustProductStockUseCase(
        MongoProductRepository(database),
    ).execute(sku, delta, reason, operation_id or str(uuid4())))


def test_real_stock_transaction_replay_conflict_and_atomic_history(database):
    operation_id = str(uuid4())
    before_orders = list(database.orders.find({}))
    first = adjust(database, "TEST-BOSCH-X100", 4, operation_id)
    replay = adjust(database, "TEST-BOSCH-X100", 4, operation_id)
    assert replay == first
    assert database.products.find_one({"sku": "TEST-BOSCH-X100"})["stock"] == 14
    assert database["stock_movements"].count_documents({}) == 1
    with pytest.raises(StockAdjustmentConflictError):
        adjust(database, "TEST-BOSCH-X100", 5, operation_id)
    with pytest.raises(InsufficientStockError):
        adjust(database, "TEST-BOSCH-X100", -15)
    assert database.products.find_one({"sku": "TEST-BOSCH-X100"})["stock"] == 14
    assert database["stock_movements"].count_documents({}) == 1
    assert list(database.orders.find({})) == before_orders

    stored = database["stock_movements"].find_one({"operation_id": operation_id})
    product = database.products.find_one({"sku": "TEST-BOSCH-X100"})
    assert stored["product_id"] == product["_id"]
    assert stored["stock_before"] == 10 and stored["stock_after"] == 14
    assert stored["source"] == "BACKOFFICE" and stored["reason"] == "integration recount"
    assert "_id" not in first.__dict__ and "product_id" not in first.__dict__


def test_real_concurrent_same_operation_commits_once(database):
    operation_id = str(uuid4())
    barrier = Barrier(8)

    def adjust_same(_):
        barrier.wait(timeout=10)
        return adjust(database, "TEST-BOSCH-X100", 3, operation_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(adjust_same, range(8)))
    assert all(result == results[0] for result in results)
    assert database.products.find_one({"sku": "TEST-BOSCH-X100"})["stock"] == 13
    assert database["stock_movements"].count_documents({"operation_id": operation_id}) == 1


def test_real_adjustments_concurrent_with_order_reserve_and_cancel(database):
    operation = str(uuid4())
    order = make_order(operation_id=str(uuid4()))
    before_orders = database.orders.count_documents({})

    def reserve():
        return asyncio.run(MongoOrderTransaction(database).execute(order))

    with ThreadPoolExecutor(max_workers=2) as executor:
        adjust_future = executor.submit(adjust, database, "TEST-BOSCH-X100", 5, operation)
        order_future = executor.submit(reserve)
        movement = adjust_future.result(timeout=20)
        created = order_future.result(timeout=20)
    assert movement.stock_before in (8, 10)
    assert movement.stock_after == movement.stock_before + 5
    assert created.order_id == order.order_id
    assert database.products.find_one({"sku": "TEST-BOSCH-X100"})["stock"] == 13
    persisted = database.orders.find_one({"order_id": order.order_id})
    assert persisted["events"] == [{
        "status": "CREATED", "source": "SYSTEM", "occurred_at": order.created_at.isoformat(),
    }]

    def cancel():
        return asyncio.run(CancelOrderUseCase(MongoOrderRepository(database)).execute(order.order_id))

    with ThreadPoolExecutor(max_workers=2) as executor:
        cancel_future = executor.submit(cancel)
        adjustment_future = executor.submit(
            adjust, database, "TEST-BOSCH-X100", 3, str(uuid4()), "late delivery",
        )
        cancelled = cancel_future.result(timeout=20)
        adjustment_future.result(timeout=20)
    assert cancelled.status == OrderStatus.CANCELLED
    assert database.products.find_one({"sku": "TEST-BOSCH-X100"})["stock"] == 18
    final_order = database.orders.find_one({"order_id": order.order_id})
    assert [event["status"] for event in final_order["events"]] == ["CREATED", "CANCELLED"]
    assert [event["source"] for event in final_order["events"]] == ["SYSTEM", "BACKOFFICE"]
    assert database.orders.count_documents({}) == before_orders + 1


def test_real_stock_movement_history_has_stable_timestamp_tiebreak_and_pages(database):
    for _ in range(23):
        adjust(database, "TEST-BOSCH-X100", 1)
    same_time = datetime.now(timezone.utc)
    same_time = same_time.replace(microsecond=(same_time.microsecond // 1000) * 1000)
    database["stock_movements"].update_many(
        {"product_sku": "TEST-BOSCH-X100"},
        {"$set": {"occurred_at": same_time}},
    )
    first = asyncio.run(MongoProductRepository(database).list_stock_movements(
        "TEST-BOSCH-X100", 20, 0,
    ))
    second = asyncio.run(MongoProductRepository(database).list_stock_movements(
        "TEST-BOSCH-X100", 20, 20,
    ))
    assert len(first.items) == 20 and first.has_more
    assert len(second.items) == 3 and not second.has_more
    movement_ids = [item.movement_id for item in first.items + second.items]
    assert len(set(movement_ids)) == 23
    assert movement_ids == sorted(movement_ids, reverse=True)
    assert all(item.occurred_at == same_time for item in first.items + second.items)


def test_real_movement_insert_validation_failure_rolls_back_stock(database):
    database.command("collMod", "stock_movements", validator={"source": {"$eq": "REJECTED"}})
    try:
        with pytest.raises(StockAdjustmentTransactionFailedError):
            adjust(database, "TEST-BOSCH-X100", 3)
        assert database.products.find_one({"sku": "TEST-BOSCH-X100"})["stock"] == 10
        assert database["stock_movements"].count_documents({}) == 0
    finally:
        database.command("collMod", "stock_movements", validator={})


def test_real_concurrent_decrements_never_make_stock_negative(database):
    barrier = Barrier(2)
    def decrement():
        barrier.wait(timeout=10)
        try:
            return adjust(database, "TEST-BOSCH-X100", -7)
        except InsufficientStockError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=20) for future in (
            executor.submit(decrement), executor.submit(decrement),
        )]
    assert sum(item is not None for item in results) == 1
    assert database.products.find_one({"sku": "TEST-BOSCH-X100"})["stock"] == 3
    assert database["stock_movements"].count_documents({}) == 1


def test_real_same_operation_different_products_rolls_back_losing_delta(database):
    operation_id = str(uuid4())
    barrier = Barrier(2)
    def adjust_product(sku):
        barrier.wait(timeout=10)
        try:
            return adjust(database, sku, 2, operation_id)
        except StockAdjustmentConflictError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(adjust_product, ["TEST-BOSCH-X100", "TEST-BOSCH-X100-PRO"]))
    assert sum(item is not None for item in results) == 1
    assert database["stock_movements"].count_documents({}) == 1
    movement = database["stock_movements"].find_one({"operation_id": operation_id})
    for sku, initial in [("TEST-BOSCH-X100", 10), ("TEST-BOSCH-X100-PRO", 0)]:
        assert database.products.find_one({"sku": sku})["stock"] == initial + (
            2 if sku == movement["product_sku"] else 0
        )


@pytest.fixture
def database():
    uri = os.environ.get(
        "MONGO_TEST_URI",
        "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true",
    )
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    name = "stockpilot_transaction_test_" + uuid4().hex
    database = client[name]
    initialized = False
    try:
        validate_test_database(database, name)
        hello = client.admin.command("hello")
        assert hello.get("setName") == "rs0"
        assert hello["isWritablePrimary"]
        assert hello.get("logicalSessionTimeoutMinutes") is not None
        assert name not in client.list_database_names()
        assert database.list_collection_names() == []
        initialized = True
        MongoOrderRepository(database).initialize()
        prepare_stock_movement_indexes(database["stock_movements"])
        database.products.insert_many([
            {"sku": "TEST-BOSCH-X100-PRO", "name": "Bosch X100 Pro", "stock": 0},
            {"sku": "TEST-BOSCH-X100", "name": "Bosch X100", "stock": 10},
        ])
        yield database
    finally:
        try:
            if initialized:
                validate_test_database(database, name)
                # Each fixture owns a previously absent UUID database. Select
                # document IDs explicitly; retain the empty collections/indexes.
                for collection in (database.orders, database.products, database["stock_movements"],
                                   database["product_imports"]):
                    ids = [doc["_id"] for doc in collection.find({}, {"_id": 1})]
                    if ids:
                        collection.delete_many({"_id": {"$in": ids}})
                    assert collection.count_documents({}) == 0
        finally:
            client.close()


def make_order(operation_id=None):
    return Order(
        order_id=str(uuid4()),
        operation_id=operation_id or str(uuid4()),
        product_name="Bosch X100",
        quantity=2,
        status=OrderStatus.CREATED,
        created_at=datetime.now(timezone.utc),
        customer_id="integration-owner",
    )


def created_order(order):
    return replace(order, events=(
        OrderEvent(OrderStatus.CREATED, order.created_at, OrderEventSource.SYSTEM),
    ))


def assert_persisted_creation(database, order):
    document = database.orders.find_one({"order_id": order.order_id})
    product = database.products.find_one({"name": order.product_name})
    assert document["order_id"] == order.order_id
    assert document["operation_id"] == order.operation_id
    assert document["product_name"] == order.product_name
    assert document["quantity"] == order.quantity
    assert document["customer_id"] == order.customer_id
    assert document["status"] == "CREATED"
    assert document["created_at"] == order.created_at.isoformat()
    assert document["reserved_product_id"] == product["_id"]
    assert isinstance(document["reserved_product_id"], ObjectId)
    assert database.orders.count_documents({
        "order_id": order.order_id, "reserved_product_id": {"$type": "objectId"},
    }) == 1
    assert document["events"] == [{
        "status": "CREATED", "source": "SYSTEM",
        "occurred_at": order.created_at.isoformat(),
    }]
    assert database.products.find_one({"name": "Bosch X100 Pro"})["stock"] == 0
    return document


def test_real_order_indexes_are_stable_and_preserve_legacy_documents(database):
    orders = database.orders
    orders.insert_many([
        {"order_id": "legacy-1"},
        {"order_id": "legacy-2", "operation_id": None},
        {"order_id": "legacy-3"},
    ])
    before = list(orders.find({}))
    orders.create_index("product_name", name="existing_external_index")

    MongoOrderRepository(database).initialize()
    MongoOrderRepository(database).initialize()

    assert list(orders.find({})) == before
    indexes = orders.index_information()
    assert indexes["orders_order_id_unique"]["key"] == [("order_id", 1)]
    assert indexes["orders_order_id_unique"]["unique"] is True
    assert indexes["orders_operation_id_unique"]["partialFilterExpression"] == {
        "operation_id": {"$type": "string"},
    }
    assert indexes["orders_operation_id_unique"]["unique"] is True
    assert indexes["orders_customer_created_order_desc"]["key"] == [
        ("customer_id", 1), ("created_at", -1), ("order_id", -1),
    ]
    assert not indexes["orders_customer_created_order_desc"].get("unique", False)
    assert "existing_external_index" in indexes

    orders.insert_one({"order_id": "new-1", "operation_id": "operation-1"})
    with pytest.raises(DuplicateKeyError):
        orders.insert_one({"order_id": "new-1", "operation_id": "operation-2"})
    with pytest.raises(DuplicateKeyError):
        orders.insert_one({"order_id": "new-2", "operation_id": "operation-1"})


def test_real_duplicate_order_ids_fail_initialization_without_data_changes(database):
    database.orders.drop_index("orders_order_id_unique")
    database.orders.insert_many([
        {"order_id": "duplicate"},
        {"order_id": "duplicate"},
    ])
    before = list(database.orders.find({}))

    with pytest.raises(DuplicateKeyError):
        MongoOrderRepository(database).initialize()

    assert list(database.orders.find({})) == before
    assert "orders_operation_id_unique" in database.orders.index_information()


def test_real_order_query_plans_use_indexes_without_blocking_sort(database):
    repository = MongoOrderRepository(database)
    for number in range(100):
        order = make_order()
        repository.insert(Order(
            order_id=f"indexed-{number:03d}",
            operation_id=order.operation_id,
            product_name=order.product_name,
            quantity=order.quantity,
            status=order.status,
            created_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
            customer_id="customer-a" if number < 50 else "customer-b",
        ))
    sample = database.orders.find_one({"order_id": "indexed-025"})
    before = list(database.orders.find({}))

    def plan_nodes(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from plan_nodes(value)
        elif isinstance(node, list):
            for value in node:
                yield from plan_nodes(value)

    queries = [
        (
            {"find": "orders", "filter": {"order_id": sample["order_id"]}, "limit": 1},
            "orders_order_id_unique",
        ),
        (
            {"find": "orders",
             "filter": {"operation_id": {"$eq": sample["operation_id"], "$type": "string"}},
             "limit": 1},
            "orders_operation_id_unique",
        ),
        (
            {"find": "orders", "filter": {"customer_id": "customer-a"},
             "sort": {"created_at": -1, "order_id": -1}, "skip": 5, "limit": 20},
            "orders_customer_created_order_desc",
        ),
    ]
    for status in ("PROCESSING", "CANCELLED"):
        queries.append((
            {"findAndModify": "orders",
             "query": {"order_id": sample["order_id"], "status": "CREATED"},
             "update": {"$set": {"status": status}}, "new": True},
            "orders_order_id_unique",
        ))
    for query, expected_index in queries:
        result = database.command("explain", query, verbosity="executionStats")
        nodes = list(plan_nodes(result["queryPlanner"]["winningPlan"]))
        assert expected_index in {node.get("indexName") for node in nodes}
        assert not {"SORT", "COLLSCAN"} & {node.get("stage") for node in nodes}
        assert result["executionStats"]["totalDocsExamined"] <= 20
    assert list(database.orders.find({})) == before


def test_real_transaction_commits_stock_and_order(database):
    order = make_order()
    result = asyncio.run(MongoOrderTransaction(database).execute(order))
    assert result == created_order(order)
    document = assert_persisted_creation(database, order)
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 8
    assert database.orders.count_documents({"operation_id": order.operation_id}) == 1
    replay = asyncio.run(MongoOrderTransaction(database).execute(
        replace(order, order_id=str(uuid4())),
    ))
    assert replay == result
    assert database.orders.find_one({"order_id": order.order_id}) == document
    assert database.orders.count_documents({}) == 1
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 8
    assert database.products.find_one({"name": "Bosch X100 Pro"})["stock"] == 0


def test_real_transaction_rolls_back_insert_failure(database, monkeypatch):
    order = make_order()

    def fail_insert(self, order, session=None, *, reserved_product_id=None):
        raise OperationFailure("forced insertion failure", code=121)

    monkeypatch.setattr(MongoOrderRepository, "insert", fail_insert)
    with pytest.raises(OrderTransactionFailedError):
        asyncio.run(MongoOrderTransaction(database).execute(order))
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 10
    assert database.orders.count_documents({}) == 0
    assert database.products.find_one({"name": "Bosch X100 Pro"})["stock"] == 0


def test_real_concurrent_same_operation_only_decrements_once(database, monkeypatch):
    operation_id = str(uuid4())
    barrier = Barrier(2)
    lookup_state = local()
    original_lookup = MongoOrderRepository.find_by_operation_id_sync

    def synchronized_lookup(self, operation_id, session=None):
        existing = original_lookup(self, operation_id, session)
        if session is None and not getattr(lookup_state, "checked", False):
            lookup_state.checked = True
            assert existing is None
            # Force both contenders past the absent precheck before either writes.
            barrier.wait(timeout=10)
        return existing

    monkeypatch.setattr(
        MongoOrderRepository, "find_by_operation_id_sync", synchronized_lookup
    )

    def confirm():
        return asyncio.run(
            MongoOrderTransaction(database).execute(make_order(operation_id))
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm)
        second = pool.submit(confirm)
        results = [first.result(timeout=150), second.result(timeout=150)]
    assert results[0] == results[1]
    assert results[0] == created_order(results[0])
    assert_persisted_creation(database, results[0])
    assert database.orders.count_documents({"operation_id": operation_id}) == 1
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 8


def test_real_concurrent_cancellations_restore_stock_only_once(database, monkeypatch):
    order = make_order()
    assert asyncio.run(MongoOrderTransaction(database).execute(order)) == created_order(order)
    initial = assert_persisted_creation(database, order)
    barrier = Barrier(2)
    original_lookup = MongoOrderRepository.find_by_order_id

    async def synchronized_lookup(self, order_id):
        existing = await original_lookup(self, order_id)
        assert existing.status is OrderStatus.CREATED
        barrier.wait(timeout=10)
        return existing

    monkeypatch.setattr(MongoOrderRepository, "find_by_order_id", synchronized_lookup)

    def cancel():
        return asyncio.run(CancelOrderUseCase(MongoOrderRepository(database)).execute(order.order_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cancel)
        second = pool.submit(cancel)
        results = [first.result(timeout=150), second.result(timeout=150)]
    assert results[0] == results[1]
    assert results[0].status is OrderStatus.CANCELLED
    assert database.orders.count_documents({}) == 1
    assert database.orders.find_one({"order_id": order.order_id})["status"] == "CANCELLED"
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 10
    document = database.orders.find_one({"order_id": order.order_id})
    assert document["reserved_product_id"] == initial["reserved_product_id"]
    for field in ("order_id", "operation_id", "product_name", "quantity", "customer_id", "created_at"):
        assert document[field] == initial[field]
    assert document["events"][0] == initial["events"][0]
    assert len(document["events"]) == 2
    cancelled_event = document["events"][1]
    assert cancelled_event["status"] == "CANCELLED"
    assert cancelled_event["source"] == "BACKOFFICE"
    assert datetime.fromisoformat(cancelled_event["occurred_at"]) >= order.created_at
    assert results[0].events == (
        created_order(order).events[0],
        OrderEvent(OrderStatus.CANCELLED,
                   datetime.fromisoformat(cancelled_event["occurred_at"]),
                   OrderEventSource.BACKOFFICE),
    )
    assert database.products.find_one({"name": "Bosch X100 Pro"})["stock"] == 0
    replay = asyncio.run(MongoOrderRepository(database).cancel(
        order.order_id, expected_status=OrderStatus.CREATED,
    ))
    assert replay == results[0]
    assert database.orders.find_one({"order_id": order.order_id}) == document
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 10
