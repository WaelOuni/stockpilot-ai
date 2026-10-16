from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from stockpilot.application.ports.admin_session_store import SessionStoreUnavailable
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.presentation.api.admin_auth import AdminSessions, password_verifier, router


ORIGIN = "https://admin.example.test"
PASSWORD = "test-only-long-operator-password"
VERIFIER = password_verifier(PASSWORD)


def client_for(store, **settings):
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(
        admin_username="operator", admin_password_verifier=VERIFIER,
        admin_trusted_origin=ORIGIN, _env_file=None, **settings,
    )
    app.state.admin_sessions = store
    return TestClient(app, base_url=ORIGIN)


def login(client):
    return client.post("/backoffice/session/login", headers={"Origin": ORIGIN, "X-Admin-CSRF": "login"},
                       json={"username": "operator", "password": PASSWORD})


@pytest.mark.parametrize("stage", ["attempt", "create", "get", "revoke"])
def test_store_outage_is_safe_and_never_falls_back(stage):
    state = AdminSessions()
    client = client_for(state)
    response = login(client)
    csrf = response.json()["csrf_token"]
    token = client.cookies.get("__Host-stockpilot_admin")

    def unavailable(*args):
        raise SessionStoreUnavailable("a sensitive connection detail")

    setattr(state, stage, unavailable)
    if stage in {"attempt", "create"}:
        result = login(client)
    elif stage == "get":
        result = client.get("/backoffice/session/me")
    else:
        result = client.post("/backoffice/session/logout", headers={"Origin": ORIGIN, "X-Admin-CSRF": csrf})
    assert result.status_code == 503
    assert result.json() == {"detail": "Admin session storage unavailable."}
    assert "sensitive" not in result.text and token not in result.text
    assert "set-cookie" not in result.headers


def test_declaring_mongo_does_not_allow_memory_store():
    client = client_for(AdminSessions(), admin_session_store="mongo")
    assert login(client).status_code == 503
    assert client.get("/backoffice/session/me").status_code == 503


def test_memory_operator_authentication_is_closed_in_production():
    client = client_for(AdminSessions(), deployment_environment="production")
    assert login(client).status_code == 503
    assert client.get("/backoffice/session/me").status_code == 503
