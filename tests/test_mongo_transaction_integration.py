import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, local
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

from stockpilot.application.order_transaction_errors import OrderTransactionFailedError
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction


pytestmark = pytest.mark.integration


@pytest.fixture
def database():
    uri = os.environ.get(
        "MONGO_TEST_URI",
        "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true",
    )
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    name = "stockpilot_transaction_test_" + uuid4().hex
    database = client[name]
    try:
        hello = client.admin.command("hello")
        assert hello.get("setName") == "rs0"
        assert hello["isWritablePrimary"]
        MongoOrderRepository(database).initialize()
        database.products.insert_one({"name": "Bosch X100", "stock": 10})
        yield database
    finally:
        client.drop_database(name)
        client.close()


def make_order(operation_id=None):
    return Order(
        order_id=str(uuid4()),
        operation_id=operation_id or str(uuid4()),
        product_name="Bosch X100",
        quantity=2,
        status=OrderStatus.CREATED,
        created_at=datetime.now(timezone.utc),
    )


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
    assert result == order
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 8
    assert database.orders.count_documents({"operation_id": order.operation_id}) == 1


def test_real_transaction_rolls_back_insert_failure(database, monkeypatch):
    order = make_order()

    def fail_insert(self, order, session=None, *, reserved_product_id=None):
        raise OperationFailure("forced insertion failure", code=121)

    monkeypatch.setattr(MongoOrderRepository, "insert", fail_insert)
    with pytest.raises(OrderTransactionFailedError):
        asyncio.run(MongoOrderTransaction(database).execute(order))
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 10
    assert database.orders.count_documents({}) == 0


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
    assert database.orders.count_documents({"operation_id": operation_id}) == 1
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 8


def test_real_concurrent_cancellations_restore_stock_only_once(database, monkeypatch):
    order = make_order()
    assert asyncio.run(MongoOrderTransaction(database).execute(order)) == order
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
