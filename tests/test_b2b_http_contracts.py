"""Pure mode/navigation/proxy/admission contracts; no database or OIDC network."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from test_bff_auth import NOW, IdentityRepositories, MemoryStore, Provider, settings

from stockpilot.application.services.bff_sessions import BffSessionService
from stockpilot.bff import create_app, secure_transport


@pytest.mark.parametrize("query,allowed", [
    (b"mode=create_only", True),
    (b"mode=create_only&category=Lighting", True),
    (b"mode=create_only&mode=stock_only", False),
    (b"mode=create_only&organization_id=foreign", False),
])
def test_export_category_is_optional_but_query_is_unambiguous(monkeypatch, query, allowed):
    import asyncio
    from stockpilot.presentation.api.routes import b2b_business
    from stockpilot.domain.entities.tenant_stock_operation import ImportMode
    async def invoked(*args, **kwargs):
        return {"headers": ["sku"], "rows": []}
    monkeypatch.setattr(b2b_business, "invoke", invoked)
    request = Request({"type": "http", "query_string": query})
    action = b2b_business.export_products(request, ImportMode.CREATE_ONLY)
    if allowed:
        assert asyncio.run(action)["rows"] == []
    else:
        with pytest.raises(ValueError):
            asyncio.run(action)


def configuration(**kwargs):
    return settings(
        mode="b2b_business_test",
        mongo_database="stockpilot_b2b_test_" + uuid4().hex,
        **kwargs,
    )


def auth_client(config):
    repos = IdentityRepositories()
    service = BffSessionService(
        Provider(),
        MemoryStore(),
        repos,
        repos,
        repos,
        config.validated_policy(),
        clock=lambda: NOW,
    )
    return TestClient(
        create_app(config, service=service),
        base_url=config.origin,
        follow_redirects=False,
    )


def test_browser_login_navigation_preserves_pre_authentication_csrf():
    c = auth_client(configuration())
    r = c.post("/b2b/auth/login", json={"organization_id": "o1"})
    assert r.status_code == 403
    r = c.post(
        "/b2b/auth/login",
        json={"organization_id": "o1"},
        headers={"Origin": str(c.base_url).rstrip("/"), "X-BFF-CSRF": "login"},
    )
    assert r.status_code == 200 and set(r.json()) == {"authorization_url"}
    assert (
        "state=" in r.json()["authorization_url"]
        and "nonce=" in r.json()["authorization_url"]
    )
    cookie = r.headers["set-cookie"]
    assert (
        "__Host-stockpilot_oidc=" in cookie
        and "Secure" in cookie
        and "HttpOnly" in cookie
    )
    assert "SameSite=lax" in cookie and "Domain=" not in cookie


@pytest.mark.parametrize("mode", ["disabled", "b2b_auth_test"])
def test_business_routes_not_registered_in_other_modes(mode):
    config = settings(mode=mode)
    app = create_app(config)
    assert not any(getattr(r, "path", "").startswith("/b2b/api") for r in app.routes)
    c = TestClient(app, base_url=config.origin)
    assert c.get("/b2b/api/products").status_code == (
        503 if mode == "disabled" else 404
    )


@pytest.mark.parametrize(
    "database", ["stockpilot", "stockpilot_b2b_demo", "stockpilot_b2b_test_fixture"]
)
def test_business_configuration_does_not_admit_persistent_or_ambiguous_targets(
    database,
):
    config = settings(mode="b2b_business_test", mongo_database=database)
    c = TestClient(create_app(config), base_url=config.origin)
    assert c.get("/b2b/api/products").status_code == 503


def test_admission_also_bounds_auth_endpoints():
    c = auth_client(configuration(requests_per_minute=1))
    assert c.get("/b2b/auth/session").status_code == 401
    r = c.post("/b2b/auth/login", json={"organization_id": "o1"})
    assert r.status_code == 429 and r.headers["retry-after"] == "1"


def request(peer="203.0.113.1", scheme="http", headers=None):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/b2b/api/products",
            "scheme": scheme,
            "client": (peer, 1234),
            "server": ("bff.example.test", 443),
            "query_string": b"",
            "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
        }
    )


@pytest.mark.parametrize(
    "peer,headers,expected",
    [
        (
            "203.0.113.1",
            {
                "host": "bff.example.test",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "bff.example.test",
            },
            False,
        ),
        (
            "127.0.0.1",
            {
                "host": "bff.example.test",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "bff.example.test",
            },
            True,
        ),
        (
            "127.0.0.1",
            {
                "host": "forged",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "bff.example.test",
            },
            False,
        ),
        (
            "127.0.0.1",
            {
                "host": "bff.example.test",
                "x-forwarded-proto": "https,http",
                "x-forwarded-host": "bff.example.test",
            },
            False,
        ),
        ("127.0.0.1", {"host": "bff.example.test", "forwarded": "proto=https"}, False),
    ],
)
def test_forwarding_requires_raw_trusted_peer_and_exact_public_host(
    peer, headers, expected
):
    assert (
        secure_transport(
            request(peer, headers=headers),
            configuration(trusted_proxy_ips=["127.0.0.1"]),
        )
        is expected
    )


def test_exact_money_and_input_contracts():
    from pydantic import ValidationError

    from stockpilot.presentation.api.schemas.b2b import ConfirmIn, Money

    assert Money.model_validate(
        {"price_minor": 2**63 - 1, "currency": "EUR"}
    ).price_minor == str(2**63 - 1)
    with pytest.raises(ValidationError):
        ConfirmIn(operation_id="x", preparation_digest="d", confirmed=1)
