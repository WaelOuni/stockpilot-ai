import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pymongo import ReturnDocument
from pymongo.errors import OperationFailure

from test_cancel_order import FakeDatabase, document, make_order
from customer_auth import configure_customer_auth, customer_headers
from test_list_customer_orders import document as legacy_list_document, repository_for
from stockpilot.application.use_cases.create_order import CreateOrderUseCase
from stockpilot.application.use_cases.get_customer_order import GetCustomerOrderUseCase
from stockpilot.application.use_cases.list_customer_orders import ListCustomerOrdersUseCase
from stockpilot.application.use_cases.update_order_status import UpdateOrderStatusUseCase
from stockpilot.domain.entities.order_event import OrderEvent, OrderEventSource
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.main import app


@pytest.mark.parametrize("supplied_events", [
    (),
    (OrderEvent(OrderStatus.SHIPPED, datetime(2026, 10, 9, tzinfo=timezone.utc),
                OrderEventSource.BACKOFFICE),),
    (OrderEvent(OrderStatus.CREATED, datetime(2026, 10, 9, tzinfo=timezone.utc),
                OrderEventSource.CUSTOMER),) * 2,
])
def test_insert_normalizes_exactly_one_created_system_event(supplied_events):
    original = make_order(events=supplied_events)
    database = FakeDatabase()
    repository = MongoOrderRepository(database)
    created = repository.insert(original)
    expected = replace(original, events=(
        OrderEvent(OrderStatus.CREATED, original.created_at, OrderEventSource.SYSTEM),
    ))
    assert created == expected
    assert database.state["order"] == document(expected)
    assert original.events == supplied_events
    assert asyncio.run(repository.find_by_order_id(original.order_id)) == expected


def test_create_use_case_returns_repository_enriched_order():
    database = FakeDatabase()
    order = asyncio.run(CreateOrderUseCase(MongoOrderRepository(database)).execute(
        "Bosch X100", 3, operation_id="creation-events",
    ))
    assert order.events == (
        OrderEvent(OrderStatus.CREATED, order.created_at, OrderEventSource.SYSTEM),
    )
    assert database.state["order"] == document(order)


def test_created_processing_shipped_events_are_chronological_and_atomic():
    database = FakeDatabase()
    repository = MongoOrderRepository(database)
    created = asyncio.run(repository.create(make_order()))
    updater = UpdateOrderStatusUseCase(repository)
    previous = created
    for target in (OrderStatus.PROCESSING, OrderStatus.SHIPPED):
        before = datetime.now(timezone.utc)
        updated = asyncio.run(updater.execute(created.order_id, target))
        after = datetime.now(timezone.utc)
        appended = updated.events[-1]
        assert appended.status is target
        assert appended.source is OrderEventSource.BACKOFFICE
        assert before <= appended.occurred_at <= after
        assert appended.occurred_at.tzinfo == timezone.utc
        assert updated == replace(previous, status=target, events=(*previous.events, appended))
        assert database.state["order"] == document(updated)
        assert database.writes[-1] == (
            "order", {"order_id": created.order_id, "status": previous.status.value},
            {"$set": {"status": target.value}, "$push": {"events": {
                "status": target.value, "occurred_at": appended.occurred_at.isoformat(),
                "source": "BACKOFFICE",
            }}}, {"return_document": ReturnDocument.AFTER},
        )
        previous = updated
    assert [item.status for item in previous.events] == [
        OrderStatus.CREATED, OrderStatus.PROCESSING, OrderStatus.SHIPPED,
    ]
    times = [item.occurred_at for item in previous.events]
    assert times == sorted(times)
    assert database.state["stock"] == 7
    assert database.sessions == []


def test_same_status_reads_and_stale_cas_never_append_events():
    database = FakeDatabase()
    repository = MongoOrderRepository(database)
    order = asyncio.run(repository.create(make_order()))
    before = deepcopy(database.state)
    assert asyncio.run(repository.update_status(
        order.order_id, expected_status=OrderStatus.CREATED, status=OrderStatus.CREATED,
    )) == order
    assert database.writes == []
    assert database.reads == [({"order_id": order.order_id, "status": "CREATED"}, {})]
    assert asyncio.run(repository.update_status(
        order.order_id, expected_status=OrderStatus.PROCESSING, status=OrderStatus.SHIPPED,
    )) is None
    assert database.state == before
    assert len(database.writes) == 1


@pytest.mark.parametrize("stored_events", [None, []])
def test_legacy_order_has_empty_history_and_transition_does_not_invent_creation(stored_events):
    database = FakeDatabase()
    if stored_events is None:
        del database.state["order"]["events"]
    else:
        database.state["order"]["events"] = stored_events
    repository = MongoOrderRepository(database)
    assert asyncio.run(repository.find_by_order_id("order-cancel")).events == ()
    result = asyncio.run(repository.update_status(
        "order-cancel", expected_status=OrderStatus.CREATED, status=OrderStatus.PROCESSING,
    ))
    assert len(result.events) == 1
    assert result.events[0].status is OrderStatus.PROCESSING
    assert result.events[0].source is OrderEventSource.BACKOFFICE
    assert database.state["order"] == document(result)


@pytest.mark.parametrize("customer_id,source", [
    (None, OrderEventSource.BACKOFFICE), ("customer-a", OrderEventSource.CUSTOMER),
])
def test_cancellation_preserves_prior_history_and_records_actor(customer_id, source):
    database = FakeDatabase()
    repository = MongoOrderRepository(database)
    created = asyncio.run(repository.create(make_order(customer_id="customer-a")))
    before = datetime.now(timezone.utc)
    cancelled = asyncio.run(repository.cancel(
        created.order_id, expected_status=created.status, customer_id=customer_id,
    ))
    after = datetime.now(timezone.utc)
    assert cancelled.events[:-1] == created.events
    assert cancelled.events[-1].status is OrderStatus.CANCELLED
    assert cancelled.events[-1].source is source
    assert before <= cancelled.events[-1].occurred_at <= after
    assert database.state == {"stock": 10, "order": document(cancelled)}
    assert asyncio.run(repository.cancel(
        created.order_id, expected_status=created.status, customer_id=customer_id,
    )) == cancelled
    assert database.state == {"stock": 10, "order": document(cancelled)}
    assert len(database.writes) == 2


def test_event_document_round_trip_preserves_values_order_and_legacy_fields():
    original = make_order(status=OrderStatus.CANCELLED, customer_id="customer-a")
    events = (
        OrderEvent(OrderStatus.CREATED, original.created_at, OrderEventSource.SYSTEM),
        OrderEvent(OrderStatus.PROCESSING, original.created_at + timedelta(hours=1),
                   OrderEventSource.BACKOFFICE),
        OrderEvent(OrderStatus.CANCELLED, original.created_at + timedelta(hours=2),
                   OrderEventSource.CUSTOMER),
    )
    original = replace(original, events=events)
    assert MongoOrderRepository._to_order(document(original)) == original


def test_status_write_failure_preserves_status_and_history():
    database = FakeDatabase()
    repository = MongoOrderRepository(database)
    asyncio.run(repository.create(make_order()))
    before = deepcopy(database.state)
    failure = OperationFailure("status update rejected")
    database.order_errors = [failure]
    with pytest.raises(OperationFailure) as caught:
        asyncio.run(repository.update_status(
            "order-cancel", expected_status=OrderStatus.CREATED, status=OrderStatus.PROCESSING,
        ))
    assert caught.value is failure
    assert database.state == before


@pytest.mark.parametrize("route", ["detail", "list", "status", "customer-cancel", "backoffice-cancel"])
def test_all_order_routes_serialize_event_values_without_private_identifiers(monkeypatch, route):
    configure_customer_auth(monkeypatch, app)
    monkeypatch.setattr(app.state, "settings", app.state.settings.model_copy(
        update={"backoffice_api_token": SecretStr("test-only-token")},
    ))
    order = make_order(customer_id="customer-a", status=OrderStatus.CANCELLED)
    order = replace(order, events=(
        OrderEvent(OrderStatus.CREATED, order.created_at, OrderEventSource.SYSTEM),
        OrderEvent(OrderStatus.CANCELLED, order.created_at + timedelta(hours=1),
                   OrderEventSource.CUSTOMER),
    ))
    routes = {
        "detail": ("get_customer_order_use_case", "get", "/orders/order-cancel"),
        "list": ("list_customer_orders_use_case", "get", "/orders"),
        "status": ("update_order_status_use_case", "patch", "/orders/order-cancel/status"),
        "customer-cancel": ("cancel_order_use_case", "post", "/customer/orders/order-cancel/cancel"),
        "backoffice-cancel": ("cancel_order_use_case", "post", "/orders/order-cancel/cancel"),
    }
    dependency, method, path = routes[route]
    use_case = SimpleNamespace(execute=AsyncMock(return_value=[order] if route == "list" else order))
    monkeypatch.setattr(app.state, dependency, use_case)
    headers = (
        {"Authorization": "Bearer test-only-token"}
        if route in ("status", "backoffice-cancel") else customer_headers()
    )
    options = {"json": {"status": "PROCESSING"}} if route == "status" else {}
    response = getattr(TestClient(app), method)(path, headers=headers, **options)
    assert response.status_code == 200
    payload = response.json()["items"][0] if route == "list" else response.json()
    assert payload == {
        "order_id": "order-cancel", "product_name": "Bosch X100", "quantity": 3,
        "status": "CANCELLED", "created_at": "2026-10-10T00:00:00Z",
        "events": [
            {"status": "CREATED", "occurred_at": "2026-10-10T00:00:00Z", "source": "SYSTEM"},
            {"status": "CANCELLED", "occurred_at": "2026-10-10T01:00:00Z", "source": "CUSTOMER"},
        ],
    }
    assert "customer-a" not in response.text
    assert "operation-cancel" not in response.text


@pytest.mark.parametrize("route", ["detail", "list"])
def test_api_legacy_documents_without_events_return_empty_history(monkeypatch, route):
    configure_customer_auth(monkeypatch, app)
    if route == "detail":
        database = FakeDatabase(make_order(customer_id="customer-a"))
        del database.state["order"]["events"]
        before = deepcopy(database.state)
        monkeypatch.setattr(app.state, "get_customer_order_use_case",
                            GetCustomerOrderUseCase(MongoOrderRepository(database)))
        response = TestClient(app).get("/orders/order-cancel", headers=customer_headers())
        payload = response.json()
        assert database.state == before
        assert database.writes == []
    else:
        row = legacy_list_document("legacy-order")
        assert "events" not in row
        repository, _ = repository_for([row])
        monkeypatch.setattr(app.state, "list_customer_orders_use_case",
                            ListCustomerOrdersUseCase(repository))
        response = TestClient(app).get("/orders", headers=customer_headers())
        payload = response.json()["items"][0]
        assert "events" not in row
    assert response.status_code == 200
    assert set(payload) == {"order_id", "product_name", "quantity", "status", "created_at", "events"}
    assert payload["events"] == []
