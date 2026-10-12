from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from customer_auth import customer_headers
from test_admin_sessions import api, login, ORIGIN
from test_cancel_order import FakeDatabase, make_order
from stockpilot.application.use_cases.backoffice_orders import GetBackofficeOrderUseCase
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.application.use_cases.update_order_status import UpdateOrderStatusUseCase
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.main import app


@pytest.fixture
def mutation_api(api, monkeypatch):
    client, sessions, clock, settings = api
    database = FakeDatabase(make_order(customer_id="customer-a"))
    database.state["order"]["reserved_product_id"] = "product-bosch"
    repository = MongoOrderRepository(database)
    monkeypatch.setattr(app.state, "update_order_status_use_case", UpdateOrderStatusUseCase(repository))
    monkeypatch.setattr(app.state, "cancel_order_use_case", CancelOrderUseCase(repository))
    monkeypatch.setattr(app.state, "get_backoffice_order_use_case", GetBackofficeOrderUseCase(repository))
    session = login(client).json()
    headers = {"Origin": ORIGIN, "X-Admin-CSRF": session["csrf_token"]}
    return client, database, headers, sessions, clock, settings


def mutate(client, headers, action="status", status="PROCESSING", order_id="order-cancel"):
    return client.request("PATCH" if action == "status" else "POST",
                          f"/backoffice/orders/{order_id}/{action}", headers=headers,
                          **({"json": {"status": status}} if action == "status" else {}))


@pytest.mark.parametrize("current", list(OrderStatus))
@pytest.mark.parametrize("target", list(OrderStatus))
def test_complete_status_matrix_and_no_op(mutation_api, current, target):
    client, database, headers, *_ = mutation_api
    database.state["order"]["status"] = current.value
    before = deepcopy(database.state)
    response = mutate(client, headers, status=target.value)
    next_status = {"CREATED": "PROCESSING", "PROCESSING": "SHIPPED", "SHIPPED": "DELIVERED"}
    valid = current == target or next_status.get(current.value) == target.value
    assert response.status_code == (200 if valid else 409)
    if not valid or current == target:
        assert database.state == before
        assert not database.writes
    else:
        assert response.json()["status"] == target.value
        assert response.json()["customer_id"] == "customer-a"
        assert response.json()["events"][-1]["source"] == "BACKOFFICE"
        assert len(database.writes) == 1
    assert database.state["stock"] == 7


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("status", list(OrderStatus))
def test_cancel_replay_stock_once_and_legacy(mutation_api, status, legacy):
    client, database, headers, *_ = mutation_api
    database.state["order"]["status"] = status.value
    if legacy:
        database.state["order"].pop("reserved_product_id")
        database.state["order"].pop("customer_id")
        database.state["order"].pop("events")
    response = mutate(client, headers, "cancel")
    allowed = status is OrderStatus.CANCELLED or (not legacy and status in (OrderStatus.CREATED, OrderStatus.PROCESSING))
    assert response.status_code == (200 if allowed else 409)
    changed = not legacy and status in (OrderStatus.CREATED, OrderStatus.PROCESSING)
    assert database.state["stock"] == (10 if changed else 7)
    before = deepcopy(database.state)
    writes = deepcopy(database.writes)
    replay = mutate(client, headers, "cancel")
    assert replay.status_code == response.status_code
    assert database.state == before
    assert len(database.writes) == len(writes)
    if allowed:
        body = response.json()
        assert body["customer_id"] == (None if legacy else "customer-a")
        assert body["events"] == replay.json()["events"]
        assert len(body["events"]) == int(changed)
        if changed:
            assert body["events"][-1]["source"] == "BACKOFFICE"
        assert set(body) == {"order_id", "customer_id", "product_name", "quantity", "status", "created_at", "events"}


@pytest.mark.parametrize("action", ["status", "cancel"])
@pytest.mark.parametrize("violation", [
    "missing-session", "expired", "revoked", "bearer", "jwt", "jwt-with-cookie",
    "no-origin", "wrong-origin", "duplicate-origin", "no-csrf", "wrong-csrf",
    "duplicate-csrf", "other-session-csrf", "unconfigured",
])
def test_guards_precede_every_business_read(mutation_api, action, violation, monkeypatch):
    client, database, headers, sessions, clock, settings = mutation_api
    headers = dict(headers)
    expected = 403
    if violation in ("missing-session", "bearer", "jwt"):
        client.cookies.clear()
        expected = 401
    if violation == "bearer":
        headers["Authorization"] = f"Bearer {settings.backoffice_api_token.get_secret_value()}"
    if violation in ("jwt", "jwt-with-cookie"):
        headers.update(customer_headers())
        expected = 401
    if violation == "expired":
        clock[0] += settings.admin_session_ttl_seconds
        expected = 401
    if violation == "revoked":
        cookie = client.cookies.get("__Host-stockpilot_admin")
        assert client.post("/backoffice/session/logout", headers=headers).status_code == 204
        client.cookies.set("__Host-stockpilot_admin", cookie)
        expected = 401
    if violation == "no-origin":
        headers.pop("Origin")
    if violation == "wrong-origin":
        headers["Origin"] = ORIGIN + "/"
    if violation == "no-csrf":
        headers.pop("X-Admin-CSRF")
    if violation == "wrong-csrf":
        headers["X-Admin-CSRF"] = "wrong"
    if violation == "other-session-csrf":
        other = TestClient(app, base_url=ORIGIN)
        headers["X-Admin-CSRF"] = login(other).json()["csrf_token"]
    if violation in ("duplicate-origin", "duplicate-csrf"):
        headers = [*headers.items(), ("Origin" if violation == "duplicate-origin" else "X-Admin-CSRF",
                                     ORIGIN if violation == "duplicate-origin" else headers["X-Admin-CSRF"])]
    if violation == "unconfigured":
        monkeypatch.setattr(app.state, "settings", settings.model_copy(update={"admin_username": None}))
        expected = 503
    before = deepcopy(database.state)
    response = mutate(client, headers, action)
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    assert database.state == before
    assert not database.reads and not database.writes


@pytest.mark.parametrize("action", ["status", "cancel"])
def test_missing_order(mutation_api, action):
    client, database, headers, *_ = mutation_api
    assert mutate(client, headers, action, order_id="missing").status_code == 404
    assert not database.writes


@pytest.mark.parametrize("action", ["status", "cancel"])
def test_compare_and_swap_conflict_rolls_back(mutation_api, action):
    client, database, headers, *_ = mutation_api
    database.order_no_match = True
    before = deepcopy(database.state)
    assert mutate(client, headers, action).status_code == 409
    assert database.state == before
    if action == "cancel":
        assert database.sessions[0].aborts == 1


def test_concurrent_shipping_blocks_cancel_without_restock(mutation_api):
    client, database, headers, *_ = mutation_api
    database.state["order"]["status"] = "PROCESSING"
    database.before_attempt = lambda db, _: db.state["order"].update(status="SHIPPED")
    assert mutate(client, headers, "cancel").status_code == 409
    assert database.state["stock"] == 7
    assert database.state["order"]["status"] == "SHIPPED"
    assert not database.writes


def test_concurrent_cancel_returns_committed_result_without_second_restock(mutation_api):
    client, database, headers, *_ = mutation_api

    def competing_cancel(db, _):
        db.state["order"].update(status="CANCELLED", events=[{
            "status": "CANCELLED", "source": "BACKOFFICE",
            "occurred_at": "2026-10-10T12:00:00+00:00",
        }])
        db.state["stock"] = 10

    database.before_attempt = competing_cancel
    response = mutate(client, headers, "cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert len(response.json()["events"]) == 1
    assert database.state["stock"] == 10
    assert not database.writes


@pytest.mark.parametrize("action", ["status", "cancel"])
def test_previous_login_csrf_cannot_mutate_rotated_session(mutation_api, action):
    client, database, headers, *_ = mutation_api
    assert login(client).status_code == 200
    assert mutate(client, headers, action).status_code == 403
    assert not database.reads and not database.writes


def test_read_bearer_and_technical_mutations_remain_compatible(mutation_api):
    client, database, headers, _, _, settings = mutation_api
    bearer = {"Authorization": f"Bearer {settings.backoffice_api_token.get_secret_value()}"}
    assert client.get("/backoffice/orders/order-cancel", headers=bearer).status_code == 200
    assert mutate(client, {**headers, **bearer}).status_code == 401
    technical = client.patch("/orders/order-cancel/status", headers=bearer, json={"status": "PROCESSING"})
    assert technical.status_code == 200 and "customer_id" not in technical.json()
    assert client.post("/orders/order-cancel/cancel", headers=bearer).status_code == 200
    assert database.state["stock"] == 10
    assert [event["source"] for event in database.state["order"]["events"]] == ["BACKOFFICE", "BACKOFFICE"]
