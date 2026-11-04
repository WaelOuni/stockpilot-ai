"""Contact tests against existing guarded disposable Mongo fixtures only."""
from dataclasses import replace
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_b2b_http_mongo_integration import http, db, orders, stock
from test_tenant_orders_mongo_integration import REQUESTS, context
from stockpilot.application.services.tenant_contact import TenantContact
from stockpilot.infrastructure.persistence.mongodb.tenant_contact import MongoTenantContact
from stockpilot.domain.entities.tenant_contact import CreateContact, ReplyContact, SetContactStatus, ContactQuery
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.ports.tenant_contact import ContactRateLimited
from stockpilot.application.ports.tenant_knowledge import TenantDataConflict, TenantDataNotFound

pytestmark = pytest.mark.integration


@pytest.fixture
def contact(orders, stock, db):
    auth = orders[0].authorization
    return TenantContact(MongoTenantContact(db, auth), auth)


def call(contact, orders, method, *args, actor="a1"):
    return getattr(contact, method)(REQUESTS[actor], context(orders, actor), *args)


def create(contact, orders, actor="a1", op=None, subject="Question"):
    return call(contact, orders, "create", CreateContact(op or uuid4().hex, subject, "access", "Synthetic request"), actor=actor)


def test_request_ownership_admin_reply_and_status_without_business_changes(contact, orders, db):
    before = {n: list(db[n].find({})) for n in ("b2b_orders", "b2b_order_operations", "b2b_products", "b2b_stock_movements")}
    receipt = create(contact, orders)
    rid = receipt["request_id"]
    assert len(rid) == 32
    assert call(contact, orders, "get", rid)["created_by"] == "a1"
    assert call(contact, orders, "list", ContactQuery())["total"] == 1
    assert call(contact, orders, "list", ContactQuery(), actor="a3")["total"] == 0
    assert call(contact, orders, "list", ContactQuery(scope="organization"), actor="admin-a")["total"] == 1
    for actor in ("a3", "a2", "b1", "admin-b"):
        with pytest.raises(TenantDataNotFound):
            call(contact, orders, "get", rid, actor=actor)
        with pytest.raises(TenantDataNotFound):
            call(contact, orders, "replies", rid, 10, 0, actor=actor)
    for actor in ("a1", "a2"):
        with pytest.raises(AuthorizationDenied):
            call(contact, orders, "reply", ReplyContact("denied", rid, "forged"), actor=actor)
        with pytest.raises(AuthorizationDenied):
            call(contact, orders, "set_status", SetContactStatus("denied", rid, "resolved"), actor=actor)
        with pytest.raises(AuthorizationDenied):
            call(contact, orders, "list", ContactQuery(scope="organization"), actor=actor)
    reply = ReplyContact("reply", rid, "<script>alert(1)</script> is literal text")
    assert call(contact, orders, "reply", reply, actor="admin-a") == call(contact, orders, "reply", reply, actor="admin-a")
    assert call(contact, orders, "replies", rid, 10, 0)["items"][0]["body"] == reply.body
    for state in ("resolved", "open"):
        command = SetContactStatus(state, rid, state)
        assert call(contact, orders, "set_status", command, actor="admin-a") == call(contact, orders, "set_status", command, actor="admin-a")
        assert call(contact, orders, "get", rid)["status"] == state
    call(contact, orders, "set_status", SetContactStatus("resolved", rid, "resolved"), actor="admin-a")
    assert call(contact, orders, "get", rid)["status"] == "open"  # old replay does not revert a reopen
    assert {n: list(db[n].find({})) for n in before} == before


@pytest.mark.parametrize("role", ["READ_ONLY", "USER", "MANAGER", "ORGANIZATION_ADMIN"])
def test_all_authenticated_roles_can_create_and_read_own(contact, orders, db, role):
    db.b2b_memberships.update_one({"membership_id": "m-a1"}, {"$set": {"role": role}})
    rid = create(contact, orders)["request_id"]
    assert call(contact, orders, "get", rid)["subject"] == "Question"


def test_retry_conflicting_payload_persistent_quota_and_revocation(contact, orders, db):
    first = create(contact, orders, op="same")
    assert create(contact, orders, op="same") == first
    with pytest.raises(TenantDataConflict):
        create(contact, orders, op="same", subject="Changed")
    for _ in range(4):
        create(contact, orders)
    with pytest.raises(ContactRateLimited):
        create(contact, orders)
    assert create(contact, orders, op="same") == first  # receipt before quota
    restarted = TenantContact(MongoTenantContact(db, orders[0].authorization), orders[0].authorization)
    with pytest.raises(ContactRateLimited):
        create(restarted, orders)
    assert db.b2b_contact_requests.count_documents({}) == 5
    assert db.b2b_contact_operations.count_documents({}) == 5
    db.b2b_memberships.update_one({"membership_id": "m-a1"}, {"$set": {"status": "suspended"}})
    with pytest.raises(AuthorizationDenied):
        call(contact, orders, "get", first["request_id"])


def test_simultaneous_same_operation_creates_only_one_request(contact, orders, db):
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: create(contact, orders, op="concurrent"), range(2)))
    assert results[0] == results[1]
    assert db.b2b_contact_requests.count_documents({}) == 1
    assert db.b2b_contact_operations.count_documents({}) == 1


def test_request_and_reply_pagination_are_scoped_and_stable(contact, orders, db):
    rid = create(contact, orders)["request_id"]
    template = db.b2b_contact_requests.find_one({"request_id": rid})
    for i in range(104):
        row = {**template, "request_id": f"{i:032x}"}; row.pop("_id"); db.b2b_contact_requests.insert_one(row)
    create(contact, orders, actor="b1")
    collected = []
    for offset in range(0, 110, 10):
        value = call(contact, orders, "list", ContactQuery(limit=10, offset=offset))
        assert value["total"] == 105
        assert value["has_more"] == (offset < 100)
        collected += [r["request_id"] for r in value["items"]]
    assert collected == sorted([rid] + [f"{i:032x}" for i in range(104)], reverse=True)
    call(contact, orders, "reply", ReplyContact("reply", rid, "Response"), actor="admin-a")
    reply = db.b2b_contact_replies.find_one({"request_id": rid})
    for i in range(23):
        row = {**reply, "reply_id": f"{i:032x}"};row.pop("_id");db.b2b_contact_replies.insert_one(row)
    value = call(contact, orders, "replies", rid, 10, 20)
    assert value["total"] == 24 and len(value["items"]) == 4 and value["has_more"] is False


def test_http_contract_csrf_origin_input_and_expired_session(http):
    c = http.browser()
    body = {"operation_id": "create", "subject": "Question", "category": "other", "description": "Text"}
    root = "/b2b/api/contact/requests"
    assert c.post(root, json=body, headers={"X-BFF-CSRF": "wrong"}).status_code == 403
    assert c.post(root, json=body, headers={"Origin": "https://evil.invalid"}).status_code == 403
    for extra in ({"organization_id": "org-b"}, {"created_by": "admin-a"}, {"attachment": "file"}, {"description": "x" * 4001}, {"subject": " "}):
        assert c.post(root, json={**body, **extra}).status_code == 422
    response = c.post(root, json=body)
    assert response.status_code == 200, response.text
    assert c.post(root, json=body).json() == response.json()
    url = root + "/" + response.json()["request_id"]
    assert c.get(url).status_code == 200
    assert c.get(root).json()["total"] == 1
    assert c.get(root, headers={"Origin": "https://evil.invalid"}).status_code == 403
    for actor in ("a3", "a2", "b1", "admin-b"):
        assert http.browser(actor).get(url).status_code == 404
    assert http.browser("admin-a").get(root + "?scope=organization").json()["total"] == 1
    assert http.browser("a2").get(root + "?scope=organization").status_code == 403
    assert http.browser(None).get(url).status_code == 401
    # Revoke the real synthetic persisted BFF session, not a mocked permission.
    http.sessions.logout(http.tokens["a1"], "csrf-a1")
    assert c.get(url).status_code == 401


def test_upgrade0004_to0005_contact_is_additive_repeatable_and_non_destructive(db):
    from test_tenant_catalogue_mongo_integration import document
    from stockpilot.infrastructure.persistence.mongodb.schema_migrations import MigrationRunner, registry
    MigrationRunner(db, registry()[:4]).apply(confirmation=db.name)
    product = document()
    db.b2b_products.insert_one(product)
    names = db.list_collection_names()
    options = {name: db[name].options() for name in names}
    indexes = {name: list(db[name].list_indexes()) for name in names}
    history = list(db.stockpilot_schema_migrations.find().sort("_id", 1))
    runner = MigrationRunner(db)
    assert [m.version for m in runner.plan()] == [5]
    runner.apply(confirmation=db.name)
    runner.require_version(5)
    assert {name: db[name].options() for name in names} == options
    assert {name: list(db[name].list_indexes()) for name in names} == indexes
    assert list(db.stockpilot_schema_migrations.find().sort("_id", 1))[:4] == history
    assert db.b2b_products.find_one({"_id": product["_id"]}) == product
    added = set(db.list_collection_names()) - set(names)
    assert added == {"b2b_contact_requests", "b2b_contact_replies", "b2b_contact_operations"}
    assert all(db[name].count_documents({}) == 0 for name in added)
    completed = list(db.stockpilot_schema_migrations.find().sort("_id", 1))
    assert runner.plan() == ()
    runner.apply(confirmation=db.name)
    assert list(db.stockpilot_schema_migrations.find().sort("_id", 1)) == completed


def test_org_quota_is_shared_across_users_and_admin_reply_quota_is_persistent(contact, orders, db):
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    first = create(contact, orders)
    template = db.b2b_contact_operations.find_one({"request_id": first["request_id"]})
    # Synthetic ledger entries from distinct active/previous authors in the same org.
    for i in range(49):
        row = {**template, "user_id": "synthetic-" + str(i), "operation_id": uuid4().hex, "created_at": now}
        row.pop("_id")
        db.b2b_contact_operations.insert_one(row)
    with pytest.raises(ContactRateLimited):
        create(contact, orders, actor="a3")
    assert create(contact, orders, actor="b1")["request_id"] != first["request_id"]
    for i in range(20):
        call(contact, orders, "reply", ReplyContact("answer-" + str(i), first["request_id"], "Synthetic answer"), actor="admin-a")
    with pytest.raises(ContactRateLimited):
        call(contact, orders, "reply", ReplyContact("too-many", first["request_id"], "Synthetic answer"), actor="admin-a")
    assert db.b2b_contact_replies.count_documents({}) == 20
    assert call(contact, orders, "reply", ReplyContact("answer-0", first["request_id"], "Synthetic answer"), actor="admin-a")["reply_id"]


def test_http_admin_lifecycle_errors_pagination_and_rate_limit(http):
    c, admin = http.browser(), http.browser("admin-a")
    root = "/b2b/api/contact/requests"
    body = {"operation_id": "initial", "subject": "Access", "category": "access", "description": "<img src=x onerror=alert(1)>"}
    receipt = c.post(root, json=body).json()
    url = root + "/" + receipt["request_id"]
    assert c.get(url).json()["description"] == body["description"]
    reply = {"operation_id": "reply", "body": "Approved as plain text"}
    assert c.post(url + "/replies", json=reply).status_code == 403
    assert http.browser("a2").post(url + "/replies", json=reply).status_code == 403
    assert http.browser("admin-b").post(url + "/replies", json=reply).status_code == 404
    assert admin.post(url + "/replies", json={**reply, "body": "x" * 4001}).status_code == 422
    assert admin.post(url + "/replies", json=reply).status_code == 200
    assert c.get(url + "/replies").json()["total"] == 1
    for status in ("resolved", "open"):
        command = {"operation_id": status, "status": status}
        assert admin.post(url + "/status", json=command).status_code == 200
        assert c.get(url).json()["status"] == status
    for query in ("limit=101", "offset=10001", "offset=-1", "limit=1&limit=2", "scope=global", "created_by=a2"):
        assert c.get(root + "?" + query).status_code == 422
    for i in range(4):
        assert c.post(root, json={**body, "operation_id": str(i)}).status_code == 200
    limited = c.post(root, json={**body, "operation_id": "quota"})
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "600"
    assert limited.json() == {"code": "CONTACT_RATE_LIMITED"}
    assert c.post(root, json=body).json() == receipt
