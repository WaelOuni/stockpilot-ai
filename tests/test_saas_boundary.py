"""The shared installation must never be mistaken for an isolated SaaS."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import jwt
from pydantic import ValidationError
import pytest

from customer_auth import CUSTOMER_JWT_SECRET, customer_headers, customer_token
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.main import app


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(app.state, "settings", Settings(customer_jwt_secret=CUSTOMER_JWT_SECRET))
    doubles = []
    for name in list(app.state._state):
        if name.endswith("_use_case") or name in {
            "preview_product_csv_import", "execute_product_csv_import", "get_product_csv_import_result",
        }:
            double = SimpleNamespace(
                execute=AsyncMock(side_effect=AssertionError("Access must precede data/LLM work")),
                execute_page=AsyncMock(side_effect=AssertionError("No paginated data work")),
                max_bytes=1024,
            )
            monkeypatch.setattr(app.state, name, double)
            doubles.extend([double.execute, double.execute_page])
    workflow = SimpleNamespace(ainvoke=AsyncMock(side_effect=AssertionError("No workflow")))
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    doubles.append(workflow.ainvoke)
    yield TestClient(app), doubles
    for double in doubles:
        double.assert_not_called()


PROTECTED = [("GET", "/products"), ("GET", "/products/SKU"), ("POST", "/chat"), ("POST", "/classify")]


@pytest.mark.parametrize("method,path", PROTECTED)
@pytest.mark.parametrize("headers", [
    {}, {"X-Customer-Id": "customer-a", "X-Tenant-Id": "tenant-a"},
    {"Authorization": "Bearer invalid"},
    {"Authorization": "Bearer " + customer_token(exp=1)},
    {"Authorization": "Bearer " + jwt.encode({"customer_id": "a", "exp": 9999999999}, "", algorithm="none")},
])
def test_formerly_public_apis_reject_unverified_identity(api, method, path, headers):
    client, _ = api
    response = client.request(method, path, headers=headers, json={"message": "hello"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "tenant-a" not in response.text


@pytest.mark.parametrize("mode", ["production", "production_saas"])
def test_all_sensitive_routes_closed_even_with_configured_credentials(api, monkeypatch, mode):
    client, _ = api
    monkeypatch.setattr(app.state, "settings", Settings(
        deployment_environment=mode, customer_jwt_secret=CUSTOMER_JWT_SECRET,
        backoffice_api_token="test-technical-token",
    ))
    operations = [(method.upper(), path) for path, methods in app.openapi()["paths"].items()
                  for method in methods if method in {"get", "post", "patch", "put", "delete"} and path != "/health"]
    operations.extend(("GET", path) for path in ("/docs", "/openapi.json", "/redoc", "/docs/oauth2-redirect"))
    assert len(operations) >= 32
    for method, path in operations:
        path = path.replace("{sku}", "SKU").replace("{order_id}", "order").replace("{operation_id}", "operation")
        response = client.request(method, path, headers={
            **customer_headers(), "X-Tenant-Id": "tenant-a", "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Proto": "https",
        }, json={"message": "confirm", "thread_id": "thread", "status": "PROCESSING"})
        assert response.status_code == 503, (method, path)
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/health").json() == {"status": "UP"}


@pytest.mark.parametrize("mode", ["prod", "PRODUCTION", "", "local-demo", "saas"])
def test_unknown_modes_rejected(mode):
    with pytest.raises(ValidationError):
        Settings(deployment_environment=mode)


def test_issuer_and_audience_configuration_is_paired(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(app.state, "settings", Settings(
        customer_jwt_secret=CUSTOMER_JWT_SECRET, customer_jwt_issuer="https://issuer.example.test",
    ))
    assert client.get("/products", headers=customer_headers()).status_code == 503


@pytest.mark.parametrize("claims", [
    {}, {"iss": "wrong", "aud": "catalogue"}, {"iss": "https://issuer.example.test", "aud": "wrong"},
])
def test_configured_token_context_is_enforced(api, monkeypatch, claims):
    client, _ = api
    monkeypatch.setattr(app.state, "settings", Settings(
        customer_jwt_secret=CUSTOMER_JWT_SECRET,
        customer_jwt_issuer="https://issuer.example.test", customer_jwt_audience="catalogue",
    ))
    assert client.get("/products", headers={"Authorization": "Bearer " + customer_token(**claims)}).status_code == 401


def test_duplicate_authorization_rejected(api):
    client, _ = api
    token = customer_headers()["Authorization"]
    assert client.get("/products", headers=[("Authorization", token), ("Authorization", "Bearer invalid")]).status_code == 401
