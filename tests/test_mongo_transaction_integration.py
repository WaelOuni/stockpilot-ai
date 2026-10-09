import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, local
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from stockpilot.application.order_transaction_errors import OrderTransactionFailedError
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


def test_real_transaction_commits_stock_and_order(database):
    order = make_order()
    result = asyncio.run(MongoOrderTransaction(database).execute(order))
    assert result == order
    assert database.products.find_one({"name": "Bosch X100"})["stock"] == 8
    assert database.orders.count_documents({"operation_id": order.operation_id}) == 1


def test_real_transaction_rolls_back_insert_failure(database, monkeypatch):
    order = make_order()

    def fail_insert(self, order, session=None):
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
