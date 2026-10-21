"""HTTP -> real BFF sessions/identity -> real B1/B2/B3 transactions, isolated DB."""

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_tenant_orders_mongo_integration import ACTORS, ISSUER
from test_tenant_stock_operations_mongo_integration import db as db  # noqa: PLC0414 - pytest fixture re-export
from test_tenant_stock_operations_mongo_integration import orders as orders  # noqa: PLC0414 - pytest fixture re-export
from test_tenant_stock_operations_mongo_integration import stock as stock  # noqa: PLC0414 - pytest fixture re-export

from stockpilot.application.ports.bff_security import BffSession
from stockpilot.application.ports.tenant_orders import TenantOrderOutcomeUnknown
from stockpilot.application.services.bff_sessions import BffSessionService, token_hash
from stockpilot.bff import create_app
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.infrastructure.config.bff_settings import BffSettings
from stockpilot.infrastructure.identity.bff_business import BffBusiness
from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
    MongoIdentityRepository,
    MongoMembershipRepository,
    MongoOrganizationRepository,
)
from stockpilot.infrastructure.persistence.mongodb.bff_store import MongoBffStore
from stockpilot.presentation.api.routes.bff_auth import SESSION_COOKIE


class HttpContext(SimpleNamespace):
    def __repr__(self):
        return "<isolated HTTP fixture>"


pytestmark = pytest.mark.integration
ORIGIN = "https://bff.example:8443"


@pytest.fixture
def http(db, orders, stock):
    settings = BffSettings(
        mode="b2b_business_test",
        origin=ORIGIN,
        issuer=ISSUER,
        client_id="fixture",
        client_secret="x" * 40,
        configuration_key="y" * 40,
        mongo_uri="mongodb://127.0.0.1:1",
        mongo_database=db.name,
    )
    store = MongoBffStore(db)
    store.initialize()
    service = BffSessionService(
        SimpleNamespace(
            authorization_url=lambda *args: ISSUER + "/auth",
            logout_url=lambda: ISSUER + "/logout",
        ),
        store,
        MongoIdentityRepository(db),
        MongoOrganizationRepository(db),
        MongoMembershipRepository(db),
        settings.validated_policy(),
    )
    bundle = BffBusiness.compose(db, service)
    app = create_app(settings, service=service, business=bundle)
    now = datetime.now(UTC)
    tokens = {}
    for user, (org, _) in ACTORS.items():
        token = secrets.token_urlsafe(32)
        tokens[user] = token
        store.rotate_session(
            BffSession(
                token_hash(token),
                ExternalIdentity(ISSUER, user),
                user,
                org,
                "csrf-" + user,
                now,
                now + timedelta(minutes=20),
                now + timedelta(minutes=10),
                service.policy.configuration,
            ),
            None,
        )
    clients = []

    def browser(actor="a1"):
        c = TestClient(app, base_url=ORIGIN)
        if actor:
            c.cookies.set(SESSION_COOKIE, tokens[actor])
            c.headers.update({"Origin": ORIGIN, "X-BFF-CSRF": "csrf-" + actor})
        clients.append(c)
        return c

    yield HttpContext(
        db=db, app=app, browser=browser, bundle=bundle, sessions=service, tokens=tokens
    )
    for c in clients:
        c.close()


def prepare(c, operation="prepare-1", buyer="customer-org-a"):
    return c.post(
        "/b2b/api/orders/prepare",
        json={
            "operation_id": operation,
            "customer_id": buyer,
            "lines": [{"sku": "DRILL-001", "quantity": 1}],
        },
    )


def confirm(c, order, operation="confirm-1"):
    return c.post(
        "/b2b/api/orders/" + order["order_id"] + "/confirm",
        json={
            "operation_id": operation,
            "preparation_digest": order["preparation_digest"],
            "confirmed": True,
        },
    )


def fact(h, org="org-a"):
    return h.db.b2b_products.find_one({"organization_id": org, "sku": "DRILL-001"})


def test_order_lifecycle_replay_and_no_cross_tenant_mutation(http):
    h, c = http, http.browser()
    foreign_before = fact(h, "org-b")
    prepared = prepare(c)
    assert prepared.status_code == 200, prepared.text
    o = prepared.json()
    assert o["status"] == "prepared" and fact(h)["reserved"] == 0
    first = confirm(c, o)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "reserved"
    assert confirm(c, o).json() == first.json()
    assert fact(h)["quantity"] == 10 and fact(h)["reserved"] == 1
    result = c.get("/b2b/api/orders/operations/confirm/confirm-1")
    assert result.json()["order_id"] == o["order_id"]
    assert c.get("/b2b/api/orders").json()["items"][0]["order_id"] == o["order_id"]
    for _ in range(2):
        response = c.post(
            "/b2b/api/orders/" + o["order_id"] + "/cancel",
            json={"operation_id": "cancel-1"},
        )
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert fact(h)["reserved"] == 0
    assert confirm(c, o).json()["status"] == "cancelled"
    assert fact(h, "org-b") == foreign_before


@pytest.mark.parametrize(
    "actor,status", [(None, 401), ("a3", 404), ("b1", 404), ("a2", 200)]
)
def test_order_ownership(http, actor, status):
    o = prepare(http.browser()).json()
    c = http.browser(actor)
    assert c.get("/b2b/api/orders/" + o["order_id"]).status_code == status
    if actor in ("a3", "b1"):
        assert c.get("/b2b/api/orders").json()["items"] == []
        assert c.get("/b2b/api/orders/operations/prepare/prepare-1").status_code == 404
        assert (
            c.post(
                "/b2b/api/orders/" + o["order_id"] + "/cancel",
                json={"operation_id": "forged"},
            ).status_code
            == 404
        )
    assert fact(http)["reserved"] == 0


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"X-BFF-CSRF": ""}, 403),
        ({"X-BFF-CSRF": "wrong"}, 403),
        ({"Origin": "https://foreign.example"}, 403),
        ({"Host": "forged.example"}, 400),
        ({"X-Organization-Id": "org-b"}, 400),
        ({"Authorization": "Bearer forged"}, 400),
        ({"X-Forwarded-Proto": "https"}, 400),
        ({"Forwarded": "host=forged"}, 400),
    ],
)
def test_forged_headers_and_csrf_no_mutation(http, headers, status):
    c = http.browser()
    before = fact(http)
    response = c.post(
        "/b2b/api/orders/prepare",
        headers=headers,
        json={
            "operation_id": "forged",
            "customer_id": "customer-org-a",
            "lines": [{"sku": "DRILL-001", "quantity": 1}],
        },
    )
    assert response.status_code == status, response.text
    assert fact(http) == before and http.db.b2b_orders.count_documents({}) == 0
    assert response.headers.get("x-request-id")


@pytest.mark.parametrize(
    "change,status",
    [
        ("expired", 401),
        ("revoked", 401),
        ("membership", 403),
        ("organization", 403),
        ("user", 403),
    ],
)
def test_revocation_and_expiry(http, change, status):
    c = http.browser()
    if change == "expired":
        http.db.bff_sessions.update_one(
            {"_id": token_hash(http.tokens["a1"])},
            {"$set": {"absolute_expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
        )
    elif change == "revoked":
        http.sessions.store.revoke_session(
            token_hash(http.tokens["a1"]), http.sessions.policy.configuration
        )
    else:
        collection, field, value = {
            "membership": ("b2b_memberships", "user_id", "suspended"),
            "organization": ("b2b_organizations", "organization_id", "disabled"),
            "user": ("b2b_users", "user_id", "disabled"),
        }[change]
        http.db[collection].update_one(
            {field: "org-a" if change == "organization" else "a1"},
            {"$set": {"status": value}},
        )
    assert prepare(c).status_code == status
    assert http.db.b2b_orders.count_documents({}) == 0 and fact(http)["reserved"] == 0


@pytest.mark.parametrize("quantity", [True, 1.5, -1, 0, "1"])
def test_strict_payload_and_buyer(http, quantity):
    c = http.browser()
    r = c.post(
        "/b2b/api/orders/prepare",
        json={
            "operation_id": "bad",
            "customer_id": "customer-org-a",
            "lines": [{"sku": "DRILL-001", "quantity": quantity}],
        },
    )
    assert r.status_code == 422
    assert prepare(c, buyer="customer-org-b").status_code == 404
    assert http.db.b2b_orders.count_documents({}) == 0 and fact(http)["reserved"] == 0


def test_catalogue_bounded_filters_precision_and_foreign_native_id(http):
    a, b = http.browser(), http.browser("b1")
    row = fact(http)
    http.db.b2b_products.update_one(
        {"_id": row["_id"]}, {"$set": {"price_minor": 2**63 - 1, "reserved": 10}}
    )
    items = a.get("/b2b/api/products").json()["items"]
    assert {x["organization_id"] for x in items} == {"org-a"}
    assert next(x for x in items if x["sku"] == "DRILL-001")["price_minor"] == str(
        2**63 - 1
    )
    assert b.get("/b2b/api/products/" + str(row["_id"])).status_code == 404
    assert (
        b.get("/b2b/api/products/by-sku/DRILL-001").json()["organization_id"] == "org-b"
    )
    assert (
        a.get("/b2b/api/products?available=true&limit=1").json()["items"][0]["sku"]
        == "SAW-001"
    )
    stock = a.get("/b2b/api/products/" + str(row["_id"]) + "/stock").json()
    assert (stock["quantity"], stock["reserved"], stock["available"]) == (10, 10, 0)
    for path in [
        "?limit=101",
        "?offset=10001",
        "?organization_id=org-b",
        "?category[$ne]=x",
        "?limit=1&limit=2",
    ]:
        assert a.get("/b2b/api/products" + path).status_code == 422
    assert (
        a.get("/b2b/api/buyers").json()["items"][0]["customer_id"] == "customer-org-a"
    )


def test_adjustment_and_import_atomic_http(http):
    user, manager, admin = http.browser(), http.browser("a2"), http.browser("admin-a")
    foreign_before = fact(http, "org-b")
    o = prepare(user).json()
    assert confirm(user, o).status_code == 200
    row = fact(http)
    path = "/b2b/api/products/" + str(row["_id"]) + "/adjustments"
    command = {
        "operation_id": "adjust",
        "quantity": 0,
        "expected_revision": row["revision"],
        "reason": "inventory",
    }
    assert user.post(path, json=command).status_code == 403
    rejected = manager.post(path, json=command)
    assert rejected.status_code == 409 and rejected.json()["status"] == "rejected"
    assert fact(http)["reserved"] == 1 and fact(http)["quantity"] == 10
    command.update(operation_id="adjust-ok", quantity=8)
    ok = manager.post(path, json=command)
    assert ok.status_code == 200, ok.text
    assert manager.post(path, json=command).json() == ok.json()
    assert fact(http)["quantity"] == 8 and fact(http)["reserved"] == 1
    assert len(manager.get("/b2b/api/stock/movements").json()["items"]) == 1
    assert (
        user.post(
            "/b2b/api/imports/preview?mode=stock_only",
            content=b"sku,quantity\nDRILL-001,7\n",
            headers={"Content-Type": "text/csv"},
        ).status_code
        == 403
    )
    preview = admin.post(
        "/b2b/api/imports/preview?mode=stock_only",
        content=b"sku,quantity\nDRILL-001,7\n",
        headers={"Content-Type": "text/csv"},
    ).json()
    assert fact(http)["quantity"] == 8
    body = {
        "operation_id": "import-1",
        "import_id": preview["import_id"],
        "preview_digest": preview["preview_digest"],
        "confirmed": True,
    }
    other = http.browser("admin-b")
    assert other.post("/b2b/api/imports/apply", json=body).status_code == 409
    for _ in range(2):
        r = admin.post("/b2b/api/imports/apply", json=body)
        assert r.status_code == 200, r.text
    assert fact(http)["quantity"] == 7 and fact(http)["reserved"] == 1
    assert len(admin.get("/b2b/api/stock/movements").json()["items"]) == 2
    assert (
        admin.get("/b2b/api/stock/operations/import/import-1").json()["status"]
        == "succeeded"
    )
    assert fact(http, "org-b") == foreign_before


def test_timeout_after_commit_reconciles_with_original_key(http, monkeypatch):
    c = http.browser()
    o = prepare(c).json()
    original = http.bundle.orders.confirm

    def lost(*args):
        original(*args)
        raise TenantOrderOutcomeUnknown()

    monkeypatch.setattr(http.bundle.orders, "confirm", lost)
    r = confirm(c, o)
    assert r.status_code == 503 and r.json()["code"] == "OUTCOME_UNKNOWN"
    assert r.json()["operation"] == {
        "namespace": "confirm",
        "operation_id": "confirm-1",
    }
    assert fact(http)["reserved"] == 1
    r = c.get("/b2b/api/orders/operations/confirm/confirm-1")
    assert r.status_code == 200 and r.json()["status"] == "reserved"
    assert (
        http.browser("b1")
        .get("/b2b/api/orders/operations/confirm/confirm-1")
        .status_code
        == 404
    )
    monkeypatch.setattr(http.bundle.orders, "confirm", original)
    assert confirm(c, o).status_code == 200 and fact(http)["reserved"] == 1


def test_invalid_imports_limits_and_closed_historical_routes(http):
    c = http.browser("admin-a")
    assert (
        c.post(
            "/b2b/api/imports/preview?mode=stock_only", content=b"x" * (256 * 1024 + 1)
        ).status_code
        == 413
    )
    r = c.post(
        "/b2b/api/imports/preview?mode=stock_only",
        content=b"sku,quantity\n" + b"".join(f"X{i},3\n".encode() for i in range(101)),
        headers={"Content-Type": "text/csv"},
    )
    assert r.status_code == 200 and r.json()["status"] == "invalid"
    assert any(x["code"] == "row_limit" for x in r.json()["issues"])
    for path in [
        "/assistant",
        "/products",
        "/orders",
        "/backoffice/products",
        "/b2b/api/assistant",
    ]:
        assert c.get(path).status_code == 404
    assert fact(http)["quantity"] == 10 and http.db.b2b_orders.count_documents({}) == 0


def test_conflicting_operation_and_forged_payload(http):
    c = http.browser()
    assert prepare(c).status_code == 200
    body = {
        "operation_id": "prepare-1",
        "customer_id": "customer-org-a",
        "lines": [{"sku": "DRILL-001", "quantity": 2}],
    }
    assert c.post("/b2b/api/orders/prepare", json=body).status_code == 409
    body["organization_id"] = "org-b"
    assert c.post("/b2b/api/orders/prepare", json=body).status_code == 422
    assert http.db.b2b_orders.count_documents({}) == 1 and fact(http)["reserved"] == 0
    assert (
        c.post(
            "/b2b/api/orders/not-prepared/confirm",
            json={
                "operation_id": "bad-confirm",
                "preparation_digest": "forged",
                "confirmed": True,
            },
        ).status_code
        == 404
    )


def test_revoke_buyer_after_list(http):
    c = http.browser()
    assert c.get("/b2b/api/buyers").status_code == 200
    http.db.b2b_buyers.update_one({"user_id": "a1"}, {"$set": {"status": "disabled"}})
    assert prepare(c).status_code == 404
    assert http.db.b2b_orders.count_documents({}) == 0


def test_preview_expired_and_membership_revoked(http):
    c = http.browser("admin-a")

    def preview():
        r = c.post(
            "/b2b/api/imports/preview?mode=stock_only",
            content=b"sku,quantity\nDRILL-001,8\n",
            headers={"Content-Type": "text/csv"},
        )
        assert r.status_code == 200
        return r.json()

    p = preview()
    http.bundle.stock.clock = lambda: datetime.now(UTC) + timedelta(minutes=31)
    body = {
        "operation_id": "expired",
        "import_id": p["import_id"],
        "preview_digest": p["preview_digest"],
        "confirmed": True,
    }
    r = c.post("/b2b/api/imports/apply", json=body)
    assert r.status_code == 409 and r.json()["status"] == "rejected"
    assert (
        fact(http)["quantity"] == 10
        and http.db.b2b_stock_movements.count_documents({}) == 0
    )
    http.db.b2b_memberships.update_one(
        {"user_id": "admin-a"}, {"$set": {"status": "suspended"}}
    )
    assert c.post("/b2b/api/imports/apply", json=body).status_code == 403


def test_mongo_network_unavailable_does_not_become_denial_or_empty_page(
    http, monkeypatch
):
    from pymongo import MongoClient

    client = MongoClient(
        "mongodb://127.0.0.1:1", serverSelectionTimeoutMS=30, connectTimeoutMS=30
    )
    try:
        monkeypatch.setattr(
            http.sessions, "identities", MongoIdentityRepository(client[http.db.name])
        )
        r = prepare(http.browser())
        assert r.status_code == 503
        assert "mongodb" not in r.text and "127.0.0.1" not in r.text
        assert fact(http)["reserved"] == 0
    finally:
        client.close()


def test_session_rotation_invalidates_preparation_and_old_cookie(http):
    c = http.browser()
    o = prepare(c).json()
    old = http.sessions.store.get_session(
        token_hash(http.tokens["a1"]),
        http.sessions.policy.configuration,
        datetime.now(UTC),
        600,
    )
    new = secrets.token_urlsafe(32)
    http.sessions.store.rotate_session(
        replace(old, session_hash=token_hash(new), csrf_token="new-csrf"),
        old.session_hash,
    )
    assert c.get("/b2b/api/products").status_code == 401
    c.cookies.set(SESSION_COOKIE, new)
    c.headers["X-BFF-CSRF"] = "new-csrf"
    assert confirm(c, o).status_code == 409
    assert fact(http)["reserved"] == 0


def test_read_only_and_strict_boolean(http):
    c = http.browser()
    http.db.b2b_memberships.update_one(
        {"user_id": "a1"}, {"$set": {"role": "READ_ONLY"}}
    )
    assert c.get("/b2b/api/products").status_code == 200
    assert prepare(c).status_code == 403
    manager = http.browser("a2")
    response = manager.post(
        "/b2b/api/orders/no/confirm",
        json={
            "operation_id": "strict",
            "preparation_digest": "none",
            "confirmed": 1,
        },
    )
    assert response.status_code == 422 and fact(http)["reserved"] == 0


def test_downstream_timeout_is_not_body_timeout(http, monkeypatch):
    c = http.browser()
    o = prepare(c).json()
    original = http.bundle.orders.confirm

    def lost(*args):
        original(*args)
        raise TimeoutError()

    monkeypatch.setattr(http.bundle.orders, "confirm", lost)
    response = confirm(c, o)
    assert response.status_code == 503
    assert response.json()["code"] == "OUTCOME_UNKNOWN"
    assert response.json()["operation"]["operation_id"] == "confirm-1"
    assert fact(http)["reserved"] == 1
    assert c.get("/b2b/api/orders/operations/confirm/confirm-1").status_code == 200


def test_body_deadline_precedes_dispatch(http):
    import asyncio

    import httpx

    async def run():
        async def slow():
            yield b'{"operation_id":'
            await asyncio.sleep(0.1)
            yield b'"late"}'

        http.app.state.bff_settings.body_timeout_seconds = 0.01
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=http.app), base_url=ORIGIN
        ) as c:
            return await c.post(
                "/b2b/api/orders/prepare",
                content=slow(),
                headers={
                    "Cookie": SESSION_COOKIE + "=" + http.tokens["a1"],
                    "Origin": ORIGIN,
                    "X-BFF-CSRF": "csrf-a1",
                    "Content-Type": "application/json",
                },
            )

    response = asyncio.run(run())
    assert response.status_code == 408 and response.json()["code"] == "BODY_TIMEOUT"
    assert http.db.b2b_orders.count_documents({}) == 0 and fact(http)["reserved"] == 0


def test_concurrent_http_confirmation_keeps_one_reservation(http):
    import asyncio

    import httpx

    order = prepare(http.browser()).json()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=http.app), base_url=ORIGIN
        ) as c:
            body = {
                "operation_id": "concurrent",
                "preparation_digest": order["preparation_digest"],
                "confirmed": True,
            }
            h = {
                "Cookie": SESSION_COOKIE + "=" + http.tokens["a1"],
                "Origin": ORIGIN,
                "X-BFF-CSRF": "csrf-a1",
            }
            return await asyncio.gather(
                *[
                    c.post(
                        "/b2b/api/orders/" + order["order_id"] + "/confirm",
                        json=body,
                        headers=h,
                    )
                    for _ in range(2)
                ]
            )

    responses = asyncio.run(run())
    assert all(r.status_code in (200, 503) for r in responses)
    assert any(r.status_code == 200 for r in responses)
    assert fact(http)["reserved"] == 1
    assert (
        http.browser().get("/b2b/api/orders/operations/confirm/concurrent").status_code
        == 200
    )


def test_slash_sku_and_operation_keys_remain_exactly_addressable(http):
    from urllib.parse import quote

    c = http.browser()
    row = fact(http)
    http.db.b2b_products.update_one({"_id": row["_id"]}, {"$set": {"sku": "DRILL/A"}})
    response = c.get("/b2b/api/products/by-sku/" + quote("DRILL/A", safe=""))
    assert response.status_code == 200 and response.json()["sku"] == "DRILL/A"
    response = c.post(
        "/b2b/api/orders/prepare",
        json={
            "operation_id": "op/1",
            "customer_id": "customer-org-a",
            "lines": [{"sku": "DRILL/A", "quantity": 1}],
        },
    )
    assert response.status_code == 200
    result = c.get("/b2b/api/orders/operations/prepare/" + quote("op/1", safe=""))
    assert (
        result.status_code == 200
        and result.json()["order_id"] == response.json()["order_id"]
    )
    assert fact(http) is None
    assert http.db.b2b_products.find_one({"_id": row["_id"]})["reserved"] == 0


def test_dot_operation_reconciliation_uses_canonical_query(http):
    c = http.browser()
    order = prepare(c, operation="..").json()
    response = c.get(
        "/b2b/api/orders/operations",
        params={"namespace": "prepare", "operation_id": ".."},
    )
    assert (
        response.status_code == 200 and response.json()["order_id"] == order["order_id"]
    )
    assert (
        c.get("/b2b/api/products/by-sku", params={"sku": "DRILL-001"}).status_code
        == 200
    )
