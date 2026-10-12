import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure

from customer_auth import configure_customer_auth, customer_headers
from test_cancel_order import FakeDatabase, make_order
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.main import app


@pytest.fixture
def setup(monkeypatch):
    configure_customer_auth(monkeypatch, app)

    def create(status=OrderStatus.CREATED, customer="customer-a"):
        database = FakeDatabase(make_order(status, customer_id=customer))
        monkeypatch.setattr(app.state, "cancel_order_use_case", CancelOrderUseCase(MongoOrderRepository(database)))
        return TestClient(app), database

    return create


def request(client, **kwargs):
    return client.post("/customer/orders/order-cancel/cancel", headers=customer_headers(), **kwargs)


@pytest.mark.parametrize("status", [OrderStatus.CREATED, OrderStatus.PROCESSING])
def test_owner_cancel_restores_stock_once_and_returns_public_order(setup, status):
    client, database = setup(status)
    result = request(client, json={"customer_id": "customer-b"})
    assert result.status_code == 200
    assert result.json()["status"] == "CANCELLED"
    assert set(result.json()) == {"order_id", "product_name", "quantity", "status", "created_at", "events"}
    events = result.json()["events"]
    assert len(events) == 1
    assert set(events[0]) == {"status", "occurred_at", "source"}
    assert events[0]["status"] == "CANCELLED"
    assert events[0]["source"] == "CUSTOMER"
    assert datetime.fromisoformat(events[0]["occurred_at"]).tzinfo == timezone.utc
    assert database.state["stock"] == 10
    assert all(query["customer_id"] == "customer-a" for query, _ in database.reads)
    assert database.writes[-1][1] == {
        "order_id": "order-cancel", "customer_id": "customer-a", "status": status.value,
    }
    writes = len(database.writes)
    replay = request(client)
    assert replay.status_code == 200
    assert replay.json() == result.json()
    assert database.state["stock"] == 10
    assert len(database.writes) == writes
    assert len(database.sessions) == 1


def test_already_cancelled_does_not_start_transaction(setup):
    client, database = setup(OrderStatus.CANCELLED)
    assert request(client).status_code == 200
    assert database.state["stock"] == 7
    assert database.sessions == []


@pytest.mark.parametrize("status", [OrderStatus.SHIPPED, OrderStatus.DELIVERED])
def test_terminal_status_returns_conflict_without_mutation(setup, status):
    client, database = setup(status)
    before = deepcopy(database.state)
    assert request(client).status_code == 409
    assert database.state == before
    assert database.writes == []


@pytest.mark.parametrize("customer", ["customer-b", None, "", "missing"])
def test_foreign_legacy_and_missing_are_identical_404(setup, customer):
    client, database = setup(customer=customer)
    if customer == "missing":
        database.state["order"] = None
    before = deepcopy(database.state)
    response = request(client)
    assert response.status_code == 404
    assert response.json() == {"detail": "Commande introuvable (NOT_FOUND)."}
    assert database.reads == [({"order_id": "order-cancel", "customer_id": "customer-a"}, {})]
    assert database.state == before
    assert database.sessions == []


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}, {"X-Customer-Id": "customer-a"}])
def test_missing_invalid_jwt_rejected_before_use_case(monkeypatch, headers):
    configure_customer_auth(monkeypatch, app)
    use_case = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(app.state, "cancel_order_use_case", use_case)
    assert TestClient(app).post("/customer/orders/id/cancel", headers=headers).status_code == 401
    use_case.execute.assert_not_awaited()


def test_missing_auth_configuration(monkeypatch):
    configure_customer_auth(monkeypatch, app)
    monkeypatch.setattr(app.state, "settings", app.state.settings.model_copy(update={"customer_jwt_secret": None}))
    assert request(TestClient(app)).status_code == 503


@pytest.mark.parametrize("phase", ["stock", "order"])
def test_scoped_transaction_rolls_back_and_sanitizes_failure(setup, phase):
    client, database = setup()
    before = deepcopy(database.state)
    failure = OperationFailure("private")
    if phase == "stock":
        database.stock_error = failure
    else:
        database.order_errors = [failure]
    response = request(client)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert database.state == before
    assert database.sessions[0].aborts == 1


def test_transaction_rechecks_owner_after_initial_lookup(setup):
    client, database = setup()
    database.before_attempt = lambda db, attempt: db.state["order"].update(customer_id="customer-b")
    assert request(client).status_code == 404
    assert database.state["stock"] == 7
    assert database.writes == []


def test_direct_scoped_repository_rejects_foreign_cancelled_order():
    database = FakeDatabase(make_order(OrderStatus.CANCELLED, customer_id="customer-b"))
    assert asyncio.run(MongoOrderRepository(database).cancel(
        "order-cancel", expected_status=OrderStatus.CREATED, customer_id="customer-a",
    )) is None
    assert database.writes == []


@pytest.mark.parametrize("customer", ["", " "])
def test_blank_scoping_identity_rejected(customer):
    database = FakeDatabase()
    with pytest.raises(ValueError):
        asyncio.run(CancelOrderUseCase(MongoOrderRepository(database)).execute("order-cancel", customer_id=customer))
    with pytest.raises(ValueError):
        asyncio.run(MongoOrderRepository(database).cancel(
            "order-cancel", expected_status=OrderStatus.CREATED, customer_id=customer,
        ))
    assert database.reads == []


def test_missing_historical_document_identity_returns_manual_conflict_without_writes(setup):
    client, database = setup()
    database.state['order'].pop('reserved_product_id')
    before = deepcopy(database.state)
    response = request(client)
    assert response.status_code == 409
    assert 'MANUAL_INTERVENTION_REQUIRED' in response.json()['detail']
    assert database.state == before
    assert database.writes == []
