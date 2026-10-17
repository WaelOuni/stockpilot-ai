from pathlib import Path
from unittest.mock import AsyncMock
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from customer_auth import CUSTOMER_JWT_SECRET, customer_headers

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.presentation.api.security_http import HttpResponseHeaders, install_http_security


def client_for(*, environment="production", hosts=None, origins=None, base_url="https://saas.example.test"):
    app = FastAPI()
    app.state.settings = Settings(
        deployment_environment=environment, http_trusted_hosts=hosts or [],
        http_allowed_origins=origins or [], _env_file=None,
    )
    business = AsyncMock(return_value={"result": "private"})

    @app.get("/health")
    async def health():
        return {"status": "UP"}

    @app.api_route("/business", methods=["GET", "POST"])
    async def sensitive():
        return await business()

    @app.get("/denied")
    async def denied():
        raise HTTPException(401, "Authentication required.")

    install_http_security(app)
    app.add_middleware(HttpResponseHeaders)
    return TestClient(app, base_url=base_url), business


def test_production_without_host_configuration_closes_business_and_keeps_probe():
    client, business = client_for(base_url="http://127.0.0.1")
    assert client.get("/health").json() == {"status": "UP"}
    response = client.get("/business")
    assert response.status_code == 503
    assert "private" not in response.text
    business.assert_not_awaited()


@pytest.mark.parametrize("host", ["evil.example.test", "saas.example.test.evil.test", "www.saas.example.test"])
def test_unexpected_hosts_are_rejected_without_redirect_or_execution(host):
    client, business = client_for(hosts=["saas.example.test"])
    response = client.get("/business", headers={"Host": host}, follow_redirects=False)
    assert response.status_code == 400
    assert "location" not in response.headers
    business.assert_not_awaited()


def test_duplicate_host_headers_are_rejected():
    client, business = client_for(hosts=["saas.example.test"])
    response = client.get("/business", headers=[("Host", "saas.example.test"), ("Host", "evil.test")])
    assert response.status_code == 400
    business.assert_not_awaited()


def test_raw_forwarding_headers_cannot_establish_https_or_trusted_host():
    client, business = client_for(hosts=["saas.example.test"], base_url="http://saas.example.test")
    headers = {
        "X-Forwarded-Proto": "https", "X-Forwarded-Host": "saas.example.test",
        "X-Forwarded-For": "127.0.0.1", "Forwarded": 'for=127.0.0.1;proto=https;host=saas.example.test',
    }
    assert client.get("/business", headers=headers).status_code == 400
    assert client.get("/health", headers=headers).status_code == 200
    assert client.get("/business", headers={**headers, "Host": "evil.test"}).status_code == 400
    business.assert_not_awaited()


@pytest.mark.parametrize("origin", ["https://evil.test", "null", "http://saas.example.test"])
def test_default_same_origin_policy_refuses_cross_origin_calls_and_preflights(origin):
    client, business = client_for(hosts=["saas.example.test"])
    for method, extra in [("POST", {}), ("OPTIONS", {"Access-Control-Request-Method": "POST"})]:
        response = client.request(method, "/business", headers={"Origin": origin, **extra})
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
    business.assert_not_awaited()


def test_same_origin_request_and_nonbrowser_request_reach_business():
    client, business = client_for(hosts=["saas.example.test"])
    assert client.post("/business", headers={"Origin": "https://saas.example.test"}).status_code == 200
    assert client.get("/business").status_code == 200
    assert business.await_count == 2


def test_default_https_port_has_the_same_serialized_origin():
    client, business = client_for(hosts=["saas.example.test"], base_url="https://saas.example.test:443")
    assert client.post("/business", headers={"Origin": "https://saas.example.test", "Host": "saas.example.test:443"}).status_code == 200
    business.assert_awaited_once()


@pytest.mark.parametrize("configured_host", ["::1", "[::1]"])
def test_ipv6_host_configuration_matches_ipv6_authorities(configured_host):
    client, business = client_for(hosts=[configured_host], base_url="https://[::1]")
    assert client.post("/business", headers={"Origin": "https://[::1]"}).status_code == 200
    business.assert_awaited_once()


def test_missing_production_configuration_preserves_ipv6_loopback_probe():
    client, business = client_for(base_url="http://[::1]")
    assert client.get("/health").status_code == 200
    assert client.get("/business").status_code == 503
    business.assert_not_awaited()


def test_duplicate_origins_are_rejected():
    client, business = client_for(hosts=["saas.example.test"])
    response = client.post("/business", headers=[("Origin", "https://saas.example.test")] * 2)
    assert response.status_code == 403
    business.assert_not_awaited()


def test_explicit_cors_is_exact_and_supports_only_documented_methods_and_headers():
    client, business = client_for(hosts=["saas.example.test"], origins=["https://frontend.example.test"])
    headers = {
        "Origin": "https://frontend.example.test", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,x-admin-csrf",
    }
    response = client.options("/business", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example.test"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"]
    business.assert_not_awaited()
    assert client.options("/business", headers={**headers, "Access-Control-Request-Method": "DELETE"}).status_code == 400
    assert client.options("/business", headers={**headers, "Access-Control-Request-Headers": "x-unreviewed"}).status_code == 400
    assert client.options("/business", headers={**headers, "Origin": "https://frontend.example.test.evil.test"}).status_code == 403
    assert client.post("/business", headers={"Origin": "https://frontend.example.test"}).status_code == 200


def test_production_refuses_http_cors_configuration():
    client, business = client_for(hosts=["saas.example.test"], origins=["http://frontend.example.test"])
    assert client.get("/business").status_code == 503
    business.assert_not_awaited()


@pytest.mark.parametrize("value", ["*", "*.example.test", "https://example.test", "example.test:443", "bad host"])
def test_wildcard_and_ambiguous_hosts_cannot_be_configured(value):
    with pytest.raises(ValidationError):
        Settings(http_trusted_hosts=[value], _env_file=None)


@pytest.mark.parametrize("value", ["*", "https://*.test", "https://example.test/", "https://user:secret@example.test", "https://example.test?query", "https://example.test:0"])
def test_wildcard_and_ambiguous_origins_cannot_be_configured(value):
    with pytest.raises(ValidationError):
        Settings(http_allowed_origins=[value], _env_file=None)


@pytest.mark.parametrize("path,status", [("/business", 200), ("/denied", 401), ("/missing", 404)])
def test_api_security_headers_on_success_and_errors(path, status):
    client, _ = client_for(hosts=["saas.example.test"])
    response = client.get(path)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" not in response.headers
    assert "strict-transport-security" not in response.headers


def test_security_headers_also_cover_boundary_errors():
    client, _ = client_for(hosts=["saas.example.test"])
    response = client.get("/business", headers={"Host": "evil.test"})
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_composed_application_retains_request_ids_for_early_denials(monkeypatch):
    from stockpilot.main import app

    monkeypatch.setattr(app.state, "settings", Settings(
        deployment_environment="production", http_trusted_hosts=["saas.example.test"], _env_file=None,
    ))
    execute = AsyncMock(side_effect=AssertionError("Host rejection must precede repository work"))
    monkeypatch.setattr(app.state, "list_products_use_case", SimpleNamespace(execute=execute))
    response = TestClient(app, base_url="https://evil.test").get("/products")
    assert response.status_code == 400
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    execute.assert_not_awaited()


def test_unexpected_repository_failure_has_safe_response_and_security_headers(monkeypatch):
    from stockpilot.main import app

    monkeypatch.setattr(app.state, "settings", Settings(customer_jwt_secret=CUSTOMER_JWT_SECRET, _env_file=None))
    monkeypatch.setattr(app.state.list_products_use_case, "execute", AsyncMock(
        side_effect=RuntimeError("synthetic credential must never be disclosed"),
    ))
    response = TestClient(app).get("/products", headers=customer_headers())
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_development_keeps_local_http_and_existing_host_compatibility():
    client, business = client_for(environment="development", base_url="http://testserver")
    assert client.get("/business").status_code == 200
    business.assert_awaited_once()


def test_future_production_saas_mode_uses_the_same_closed_contract():
    client, business = client_for(environment="development", base_url="http://127.0.0.1")
    # The auth PR introduces the mode. The independent HTTP middleware must
    # already recognize it when composed, without requiring that PR's code.
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"deployment_environment": "production_saas"},
    )
    assert client.get("/health").status_code == 200
    assert client.get("/business").status_code == 503
    business.assert_not_awaited()


def test_web_document_policy_is_local_and_does_not_assume_hsts():
    root = Path(__file__).resolve().parents[1]
    template = (root / "docker/web.conf.template").read_text()
    assert 'add_header Content-Security-Policy' in template
    for directive in ("script-src 'self'", "style-src 'self'", "object-src 'none'", "frame-ancestors 'none'"):
        assert directive in template
    assert "unsafe-inline" not in template and "unsafe-eval" not in template
    assert "Strict-Transport-Security" not in template
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in template
    assert "proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto" not in template

def test_composed_middleware_stack_order_keeps_saas_boundary_innermost():
    from stockpilot.main import app
    from stockpilot.presentation.api.saas_boundary import SaasBoundaryMiddleware

    names = [middleware.cls.__name__ for middleware in app.user_middleware]
    expected = ["HttpResponseHeaders", "RequestLoggingMiddleware", "HttpSecurityBoundary",
                "SaasBoundaryMiddleware"]
    assert SaasBoundaryMiddleware.__name__ in names, "SaaS fail-closed boundary is not registered"
    assert names.count("SaasBoundaryMiddleware") == 1
    positions = [names.index(name) for name in expected]
    assert positions == sorted(positions), names


def test_input_protection_runs_inside_http_and_saas_boundaries():
    from stockpilot.main import app

    names = [middleware.cls.__name__ for middleware in app.user_middleware]
    assert names.count("InputProtectionMiddleware") == 1
    assert names.count("SaasBoundaryMiddleware") == 1
    # user_middleware is outermost first.
    assert names.index("HttpSecurityBoundary") < names.index("SaasBoundaryMiddleware")
    assert names.index("SaasBoundaryMiddleware") < names.index("InputProtectionMiddleware")


def test_saas_closed_environment_refuses_before_input_admission(monkeypatch):
    from fastapi.testclient import TestClient
    from stockpilot.infrastructure.config.settings import Settings
    from stockpilot.main import app

    settings = Settings(deployment_environment="production", http_trusted_hosts=["127.0.0.1"])
    monkeypatch.setattr(app.state, "settings", settings)
    client = TestClient(app, base_url="https://127.0.0.1")
    # Oversized, unauthenticated conversation bodies must hit the SaaS lock
    # (503), never the 401/413/429 produced by input protection.
    response = client.post("/chat", content=b"x" * (settings.http_body_max_bytes + 1),
                           headers={"content-type": "application/json"})
    assert response.status_code == 503
