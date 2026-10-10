import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.main import app
from stockpilot.presentation.api.routes.assistant import customer_thread_key
from customer_auth import (
    CUSTOMER_JWT_SECRET,
    configure_customer_auth,
    customer_headers,
    customer_token,
)


@pytest.fixture(autouse=True)
def customer_auth(monkeypatch):
    configure_customer_auth(monkeypatch, app)


def test_assistant_uses_signed_identity_and_ignores_customer_header_and_body(monkeypatch):
    workflow = SimpleNamespace(ainvoke=AsyncMock(return_value={"response": "ok"}))
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    client = TestClient(app)
    response = client.post(
        "/assistant",
        headers=customer_headers("customer-a", **{"X-Customer-Id": "customer-b"}),
        json={"message": "hello", "thread_id": "same", "customer_id": "customer-b"},
    )

    assert response.status_code == 200
    workflow.ainvoke.assert_awaited_once_with(
        {"message": "hello", "customer_id": "customer-a"},
        config={"configurable": {"thread_id": customer_thread_key("customer-a", "same")}},
    )
    operation = client.get("/openapi.json").json()["paths"]["/assistant"]["post"]
    assert operation["security"] == [{"CustomerBearer": []}]
    assert not any(parameter["name"] == "X-Customer-Id" for parameter in operation.get("parameters", []))


@pytest.mark.parametrize("headers", [
    {},
    {"X-Customer-Id": "customer-a"},
    {"Authorization": "Basic token"},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer  token"},
    {"Authorization": "Bearer token extra"},
    {"Authorization": "Bearer\ttoken"},
])
def test_missing_or_malformed_bearer_is_rejected(monkeypatch, headers):
    workflow = SimpleNamespace(ainvoke=AsyncMock())
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    response = TestClient(app).post(
        "/assistant", headers=headers,
        json={"message": "hello", "thread_id": "same"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid customer credentials."}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    workflow.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("token", [
    "malformed",
    customer_token(secret="different-signing-secret-at-least-48-bytes-long"),
    jwt.encode(
        {"customer_id": "customer-a", "exp": 4_000_000_000},
        CUSTOMER_JWT_SECRET,
        algorithm="HS384",
    ),
    jwt.encode(
        {"customer_id": "customer-a", "exp": 4_000_000_000},
        key="",
        algorithm="none",
    ),
    jwt.encode({"customer_id": "customer-a"}, CUSTOMER_JWT_SECRET, algorithm="HS256"),
    jwt.encode({"exp": 4_000_000_000}, CUSTOMER_JWT_SECRET, algorithm="HS256"),
    jwt.encode(
        {"customer_id": " ", "exp": 4_000_000_000},
        CUSTOMER_JWT_SECRET,
        algorithm="HS256",
    ),
    jwt.encode(
        {"customer_id": 42, "exp": 4_000_000_000},
        CUSTOMER_JWT_SECRET,
        algorithm="HS256",
    ),
    jwt.encode(
        {"customer_id": "customer-a", "exp": "4000000000"},
        CUSTOMER_JWT_SECRET,
        algorithm="HS256",
    ),
    jwt.encode(
        {"customer_id": "customer-a", "exp": True},
        CUSTOMER_JWT_SECRET,
        algorithm="HS256",
    ),
    jwt.encode(
        {"customer_id": "customer-a", "exp": float("inf")},
        CUSTOMER_JWT_SECRET,
        algorithm="HS256",
    ),
    jwt.encode(
        {"customer_id": "customer-a", "exp": 1},
        CUSTOMER_JWT_SECRET,
        algorithm="HS256",
    ),
])
def test_invalid_jwt_is_rejected_without_invoking_workflow(monkeypatch, token):
    workflow = SimpleNamespace(ainvoke=AsyncMock())
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    response = TestClient(app).post(
        "/assistant",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "hello", "thread_id": "same"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid customer credentials."}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    workflow.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("secret", [None, "", " \t", "short"])
def test_missing_customer_signing_configuration_fails_closed(monkeypatch, secret):
    settings = Settings(customer_jwt_secret=secret, _env_file=None)
    monkeypatch.setattr(app.state, "settings", settings)
    response = TestClient(app).post(
        "/assistant",
        headers=customer_headers(),
        json={"message": "hello", "thread_id": "same"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Customer authentication is not configured."}


def test_customer_jwt_setting_is_optional_secret_and_environment_backed(monkeypatch):
    monkeypatch.delenv("CUSTOMER_JWT_SECRET", raising=False)
    assert Settings(_env_file=None).customer_jwt_secret is None
    field = Settings.model_fields["customer_jwt_secret"]
    assert field.default is None
    assert field.repr is False
    settings = Settings(customer_jwt_secret=CUSTOMER_JWT_SECRET, _env_file=None)
    assert isinstance(settings.customer_jwt_secret, SecretStr)
    assert settings.customer_jwt_secret.get_secret_value() == CUSTOMER_JWT_SECRET
    assert CUSTOMER_JWT_SECRET not in repr(settings)
    assert CUSTOMER_JWT_SECRET not in settings.model_dump_json()
    monkeypatch.setenv("CUSTOMER_JWT_SECRET", "test-environment-secret-at-least-32")
    assert Settings(_env_file=None).customer_jwt_secret.get_secret_value() == (
        "test-environment-secret-at-least-32"
    )


def test_customer_jwt_cannot_authenticate_backoffice_or_use_backoffice_token(monkeypatch):
    token = customer_token("customer-a")
    settings = Settings(
        backoffice_api_token="test-backoffice-token",
        customer_jwt_secret=CUSTOMER_JWT_SECRET,
        _env_file=None,
    )
    monkeypatch.setattr(app.state, "settings", settings)
    response = TestClient(app).post(
        "/orders/auth-order/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid back-office credentials."}

    response = TestClient(app).post(
        "/assistant",
        headers={"Authorization": "Bearer test-backoffice-token"},
        json={"message": "hello", "thread_id": "same"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid customer credentials."}


def test_token_generator_signs_short_lived_customer_token_without_printing_secret():
    secret = "token-generator-test-secret-at-least-32"
    environment = os.environ.copy()
    environment.pop("CUSTOMER_JWT_SECRET", None)
    environment["CUSTOMER_JWT_SECRET"] = secret
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "create_customer_token.py"),
         "demo-customer"],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    claims = jwt.decode(token, secret, algorithms=["HS256"])
    assert claims["customer_id"] == "demo-customer"
    assert 0 < claims["exp"] - int(time.time()) <= 3600
    assert secret not in result.stdout + result.stderr


def test_token_generator_fails_without_configuration_or_for_blank_identity():
    project_root = Path(__file__).resolve().parents[1]
    script = str(project_root / "scripts" / "create_customer_token.py")
    environment = os.environ.copy()
    environment.pop("CUSTOMER_JWT_SECRET", None)
    missing = subprocess.run(
        [sys.executable, script, "demo-customer"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert missing.stdout == ""
    assert "CUSTOMER_JWT_SECRET" in missing.stderr

    environment["CUSTOMER_JWT_SECRET"] = "test-only-signing-secret-at-least-32"
    blank_identity = subprocess.run(
        [sys.executable, script, "  "],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert blank_identity.returncode != 0
    assert blank_identity.stdout == ""
    assert "nonblank" in blank_identity.stderr

    invalid_lifetime = subprocess.run(
        [sys.executable, script, "demo-customer", "--expires-in", "86401"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert invalid_lifetime.returncode != 0
    assert invalid_lifetime.stdout == ""
    assert "86400" in invalid_lifetime.stderr
