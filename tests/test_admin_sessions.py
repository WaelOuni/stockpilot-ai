from unittest.mock import AsyncMock
from collections import deque
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from customer_auth import CUSTOMER_JWT_SECRET, customer_headers
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.main import app
from stockpilot.domain.entities.order import Order
from stockpilot.domain.entities.order_event import OrderEvent, OrderEventSource
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.presentation.api.admin_auth import (
    AdminSessions, MAX_ATTEMPT_KEYS, password_verifier,
)


PASSWORD = "test-only-admin-password-long"
VERIFIER = password_verifier(PASSWORD)
ORIGIN = "https://admin.example.test"
LOGIN = "/backoffice/session/login"
ME = "/backoffice/session/me"
LOGOUT = "/backoffice/session/logout"
HEADERS = {"Origin": ORIGIN, "X-Admin-CSRF": "login"}
BODY = {"username": "demo-admin", "password": PASSWORD}


@pytest.fixture
def api(monkeypatch):
    settings = Settings(
        admin_username="demo-admin", admin_password_verifier=VERIFIER,
        admin_trusted_origin=ORIGIN, backoffice_api_token="technical-only-secret",
        customer_jwt_secret=CUSTOMER_JWT_SECRET, _env_file=None,
    )
    state = AdminSessions()
    clock = [1000.0]
    state.clock = lambda: clock[0]
    monkeypatch.setattr(app.state, "admin_sessions", state)
    monkeypatch.setattr(app.state, "settings", settings)
    monkeypatch.setattr(app.state, "list_backoffice_orders_use_case", AsyncMock())
    app.state.list_backoffice_orders_use_case.execute.return_value = []
    monkeypatch.setattr(app.state, "get_backoffice_order_use_case", AsyncMock())
    app.state.get_backoffice_order_use_case.execute.return_value = None
    return TestClient(app, base_url=ORIGIN), state, clock, settings


def login(client, headers=HEADERS, body=BODY):
    return client.post(LOGIN, json=body, headers=headers)


def test_secure_cookie_me_reads_logout_invalidation(api):
    client, state, _, _ = api
    assert client.get(ME).status_code == 401
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert all(part in cookie for part in ("__Host-stockpilot_admin=", "HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=1800"))
    session = response.json()
    assert set(session) == {"username", "csrf_token"}
    assert client.get(ME).json() == session
    token = client.cookies.get("__Host-stockpilot_admin")
    assert token not in response.text and PASSWORD not in response.text
    listing = client.get("/backoffice/orders")
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "no-store"
    assert client.get("/backoffice/orders/missing").status_code == 404
    result = client.post(LOGOUT, headers={"Origin": ORIGIN, "X-Admin-CSRF": session["csrf_token"]})
    assert result.status_code == 204
    assert not state.sessions
    client.cookies.set("__Host-stockpilot_admin", token)
    assert client.get(ME).status_code == 401
    assert client.get("/backoffice/orders").status_code == 401


def test_rotation_and_fixed_expiry(api):
    client, state, clock, settings = api
    first = login(client).json()
    old = client.cookies.get("__Host-stockpilot_admin")
    clock[0] += 1
    second = login(client).json()
    current = client.cookies.get("__Host-stockpilot_admin")
    assert old != current and first["csrf_token"] != second["csrf_token"]
    assert len(state.sessions) == 1 and old not in state.sessions
    assert client.post(LOGOUT, headers={"Origin": ORIGIN, "X-Admin-CSRF": first["csrf_token"]}).status_code == 403
    clock[0] += settings.admin_session_ttl_seconds - 1
    assert client.get(ME).status_code == 200
    clock[0] += 1
    assert client.get(ME).status_code == 401
    assert not state.sessions


@pytest.mark.parametrize("change", [
    {"admin_username": None}, {"admin_password_verifier": None},
    {"admin_password_verifier": PASSWORD}, {"admin_password_verifier": VERIFIER.replace("600000", "1")},
    {"admin_password_verifier": "pbkdf2_sha256$600000$" + "a" * 33 + "$" + "b" * 64},
    {"admin_trusted_origin": None}, {"admin_trusted_origin": "http://example.test"},
    {"admin_trusted_origin": ORIGIN + "/"}, {"admin_trusted_origin": ORIGIN + "?a=1"},
    {"admin_trusted_origin": "https://user:password@example.test"},
    {"admin_trusted_origin": "https://example.test:invalid"},
    {"admin_allow_local_http": True}, {"backoffice_api_token": PASSWORD},
    {"customer_jwt_secret": PASSWORD},
])
def test_configuration_fails_closed(api, change):
    client, state, _, settings = api
    for name, value in change.items():
        # Recreate settings so SecretStr conversion is retained.
        settings = Settings(**{**settings.model_dump(), name: value}, _env_file=None)
    app.state.settings = settings
    assert login(client).status_code == 503
    assert client.get(ME).status_code == 503
    assert not state.sessions


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "null", "X-Admin-CSRF": "login"},
    {"Origin": "https://evil.test", "X-Admin-CSRF": "login"},
    {"Origin": ORIGIN + "/", "X-Admin-CSRF": "login"},
    {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-Admin-CSRF": "wrong"},
    [("Origin", ORIGIN), ("Origin", ORIGIN), ("X-Admin-CSRF", "login")],
    [("Origin", ORIGIN), ("X-Admin-CSRF", "login"), ("X-Admin-CSRF", "login")],
])
def test_login_origin_and_csrf(api, headers):
    client, state, _, _ = api
    assert login(client, headers).status_code == 403
    assert not state.sessions and not state.global_attempts


@pytest.mark.parametrize("headers", [
    {}, {"Origin": ORIGIN}, {"Origin": "https://evil.test", "X-Admin-CSRF": "wrong"},
    {"Origin": ORIGIN, "X-Admin-CSRF": "wrong"},
])
def test_logout_origin_and_csrf_never_invalidate_valid_session(api, headers):
    client, state, _, _ = api
    login(client)
    assert client.post(LOGOUT, headers=headers).status_code == 403
    assert len(state.sessions) == 1 and client.get(ME).status_code == 200


def test_csrf_token_is_session_bound(api):
    client, _, _, _ = api
    one = login(client).json()
    other = TestClient(app, base_url=ORIGIN)
    two = login(other).json()
    assert one["csrf_token"] != two["csrf_token"]
    assert client.post(LOGOUT, headers={"Origin": ORIGIN, "X-Admin-CSRF": two["csrf_token"]}).status_code == 403


@pytest.mark.parametrize("body", [
    {**BODY, "password": "short"}, {**BODY, "password": "x" * 257},
    {**BODY, "unexpected": PASSWORD}, {"password": PASSWORD},
])
def test_validation_is_sanitized(api, body):
    client, _, _, _ = api
    result = login(client, body=body)
    assert result.status_code == 422
    assert result.json() == {"detail": "Invalid session request."}
    assert PASSWORD not in result.text
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("body", [
    {**BODY, "username": "unknown"}, {**BODY, "password": PASSWORD + "wrong"},
    {**BODY, "username": "démo"},
])
def test_generic_bad_credentials(api, body):
    client, state, _, _ = api
    result = login(client, body=body)
    assert result.status_code == 401
    assert result.json() == {"detail": "Invalid admin credentials."}
    assert not state.sessions


def test_throttling_window_cannot_be_reset_by_success(api):
    client, state, clock, _ = api
    assert login(client).status_code == 200
    for _ in range(4):
        assert login(client, body={**BODY, "password": PASSWORD + "wrong"}).status_code == 401
    result = login(client)
    assert result.status_code == 429 and result.headers["retry-after"] == "60"
    assert len(state.global_attempts) == 5
    clock[0] += 60
    assert login(client).status_code == 200
    assert len(state.global_attempts) == 1


def test_memory_limits_and_global_throttle(api):
    _, state, clock, _ = api
    for index in range(MAX_ATTEMPT_KEYS):
        state.attempts[str(index)] = deque([clock[0]])
    with pytest.raises(HTTPException) as error:
        state.attempt("new-address")
    assert error.value.status_code == 429
    assert len(state.attempts) == MAX_ATTEMPT_KEYS
    state.attempts.clear()
    for index in range(50):
        state.attempt(str(index))
    with pytest.raises(HTTPException) as error:
        state.attempt("another")
    assert error.value.status_code == 429
    assert len(state.global_attempts) == 50


def test_session_capacity_rotation_and_recovery(api):
    client, state, clock, settings = api
    settings.admin_max_sessions = 1
    assert login(client).status_code == 200
    other = TestClient(app, base_url=ORIGIN)
    assert login(other).status_code == 503
    assert len(state.sessions) == 1
    assert login(client).status_code == 200
    clock[0] += settings.admin_session_ttl_seconds
    assert login(other).status_code == 200
    assert len(state.sessions) == 1


def test_http_opt_in_only_local_development(api):
    _, _, _, settings = api
    settings.admin_trusted_origin = "http://127.0.0.1"
    local = TestClient(app, base_url=settings.admin_trusted_origin)
    headers = {**HEADERS, "Origin": settings.admin_trusted_origin}
    assert login(local, headers).status_code == 503
    settings.admin_allow_local_http = True
    response = login(local, headers)
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" not in response.headers["set-cookie"]
    assert "stockpilot_admin_local=" in response.headers["set-cookie"]
    settings.deployment_environment = "production"
    assert login(local, headers).status_code == 503


def test_secure_configuration_rejects_http_transport(api):
    client = TestClient(app, base_url="http://admin.example.test")
    assert login(client).status_code == 503


def test_sessions_do_not_grant_technical_mutations_or_customer_access(api):
    client, _, _, _ = api
    assert login(client).status_code == 200
    assert client.patch("/orders/id/status", json={"status": "PROCESSING"}).status_code == 401
    assert client.post("/orders/id/cancel").status_code == 401
    assert client.get("/orders").status_code == 401
    assert client.get("/backoffice/orders", headers=customer_headers()).status_code == 401
    assert client.get("/backoffice/orders", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/backoffice/orders", headers={"Authorization": "Bearer technical-only-secret"}).status_code == 200


def test_configuration_change_revokes_sessions(api):
    client, state, _, settings = api
    login(client)
    settings.admin_username = "new-user"
    assert client.get(ME).status_code == 401
    assert not state.sessions


def test_non_json_login_and_malformed_json_are_safe(api):
    client, _, _, _ = api
    result = client.post(LOGIN, content=PASSWORD, headers={**HEADERS, "Content-Type": "application/json"})
    assert result.status_code == 422 and PASSWORD not in result.text
    assert client.post(LOGIN, json=BODY, headers={**HEADERS, "Content-Type": "text/plain"}).status_code == 422


def test_session_reads_identical_global_contract_and_pagination(api):
    client, _, _, _ = api
    timestamp = datetime(2026, 10, 10, tzinfo=timezone.utc)
    order = Order("global-order", "internal-operation", "Bosch X100", 2,
                  OrderStatus.CREATED, timestamp, "another-customer",
                  (OrderEvent(OrderStatus.CREATED, timestamp, OrderEventSource.SYSTEM),))
    app.state.list_backoffice_orders_use_case.execute.return_value = [order]
    app.state.get_backoffice_order_use_case.execute.return_value = order
    assert login(client).status_code == 200
    listing = client.get("/backoffice/orders?limit=2&offset=7")
    assert listing.status_code == 200
    app.state.list_backoffice_orders_use_case.execute.assert_awaited_once_with(limit=2, offset=7)
    technical = client.get("/backoffice/orders?limit=2&offset=7",
                           headers={"Authorization": "Bearer technical-only-secret"})
    assert listing.json() == technical.json()
    detail = client.get("/backoffice/orders/global-order").json()
    assert detail == listing.json()["items"][0]
    assert detail["customer_id"] == "another-customer"
    assert detail["events"] == [{"status": "CREATED", "occurred_at": "2026-10-10T00:00:00Z", "source": "SYSTEM"}]
    assert "internal-operation" not in listing.text
    assert client.get("/backoffice/orders?offset=-1").status_code == 422


def test_restart_drops_sessions_and_failed_login_does_not_rotate(api):
    client, state, _, _ = api
    login(client)
    token = client.cookies.get("__Host-stockpilot_admin")
    assert login(client, body={**BODY, "password": PASSWORD + "wrong"}).status_code == 401
    assert token in state.sessions
    app.state.admin_sessions = AdminSessions()
    assert client.get(ME).status_code == 401
