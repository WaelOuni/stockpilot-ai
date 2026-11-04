"""Executed INSIDE each inspected API image, on Docker-owned synthetic databases.

Real Keycloak HTTPS code/PKCE/JWKS login and real BFF lifespan, HTTP routes,
Mongo transactions and PostgreSQL adapters. No pytest/mock application modules.
"""
import argparse
import asyncio
from contextlib import ExitStack
from datetime import UTC, datetime
import hashlib
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import ssl
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from bson import ObjectId, json_util
from fastapi.testclient import TestClient
from pymongo import monitoring

from integration_targets import mongo_test_client, validate_postgres_test_uri, verify_postgres_identity
try:
    from recovery_contract import (mongo_facts as recovery_mongo_facts, postgres_facts,
                                   mongo_preconditions, compare_capture, ContractError)
except ModuleNotFoundError as error:
    if error.name != "recovery_contract":
        raise
    from scripts.ops.recovery_contract import (mongo_facts as recovery_mongo_facts, postgres_facts,
                                              mongo_preconditions, compare_capture, ContractError)
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    HISTORY, LOCKS, MigrationError, MigrationRunner, registry,
)
from stockpilot.infrastructure.persistence.mongodb.b2b_identity import initialize_b2b_indexes
from stockpilot.infrastructure.persistence.mongodb.bff_store import MongoBffStore
from stockpilot.infrastructure.config.bff_settings import BffSettings
from stockpilot.bff import create_app
from stockpilot.presentation.api.routes.bff_auth import SESSION_COOKIE, TRANSACTION_COOKIE

STATE = Path("/state")
CONFIG = {}  # Explicitly loaded only by the container entrypoint, never on import.
ORIGIN = "https://bff.example:8443"
NEW = {"b2b_order_archives", "b2b_order_archive_operations", "b2b_contact_requests",
       "b2b_contact_replies", "b2b_contact_operations"}
CHECKS = []


def check(value, label):
    if not value:
        raise AssertionError(label)
    CHECKS.append(label)


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value):
    return json_util.dumps(value, sort_keys=True, json_options=json_util.CANONICAL_JSON_OPTIONS)


def save(name, value):
    (STATE / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")


def load(name):
    return json.loads((STATE / (name + ".json")).read_text())


def mongo_facts(db):
    return recovery_mongo_facts(db)


def identities(db):
    # Fences legitimately increment during authorized business transactions.
    return {n: hashed("\n".join(sorted(canonical({k: v for k, v in d.items()
               if k != "authorization_fence"}) for d in db[n].find({}))))
            for n in ("b2b_users", "b2b_memberships", "b2b_organizations", "b2b_buyers")}


BROWSER_IDENTITY_SCOPES = ("b2b_users", "b2b_organizations", "b2b_memberships")


def browser_business_facts(db):
    """Business reads fence authorization; only two synthetic principals are allowed."""
    mongo = {k: v for k, v in mongo_facts(db).items()
             if k not in {"bff_sessions", "bff_login_transactions"}}
    fences = {}
    for name in BROWSER_IDENTITY_SCOPES:
        rows = list(db[name].find({}))
        normalized, counters = [], {}
        for row in rows:
            value = row.get("authorization_fence", 0)
            check(type(value) is int and value >= 0, "typed nonnegative authorization fence")
            allowed = (row.get("organization_id") in {"org-a", "org-b"} if name == "b2b_organizations"
                       else row.get("user_id") in {"a1", "b1"})
            counters[hashed(canonical(row["_id"]))] = {
                "value": value, "present": "authorization_fence" in row, "allowed": allowed}
            normalized.append(canonical({k: v for k, v in row.items() if k != "authorization_fence"}))
        mongo[name]["data"] = hashed("\n".join(sorted(normalized)))
        fences[name] = counters
    return {"mongo": mongo, "application": pg_facts(os.environ["POSTGRES_TEST_URI"]),
            "authorization_fences": fences}


def compare_browser_business(before, after):
    check(before["mongo"] == after["mongo"],
          "browser preserves all Mongo business fields and structures outside explicit authorization counters")
    check(before["application"] == after["application"],
          "browser preserves all PostgreSQL application data and structures")
    expected, actual = before["authorization_fences"], after["authorization_fences"]
    check(set(expected) == set(actual) == set(BROWSER_IDENTITY_SCOPES), "exact authorization fence scopes")
    changes = {}
    for name in BROWSER_IDENTITY_SCOPES:
        check(set(expected[name]) == set(actual[name]), "authorization identity inventory preserved")
        delta = 0
        for identifier, old in expected[name].items():
            new = actual[name][identifier]
            check(set(old) == set(new) == {"value", "present", "allowed"}
                  and all(type(row["value"]) is int and row["value"] >= 0
                          and type(row["present"]) is bool and type(row["allowed"]) is bool
                          for row in (old, new)), "typed authorization fence proof")
            if old == new:
                continue
            check(old["allowed"] and new["allowed"] and new["present"]
                  and type(new["value"]) is int and 0 < new["value"] - old["value"] <= 100,
                  "only bounded monotonic fences of the two browser principals may change")
            delta += new["value"] - old["value"]
        changes[name] = delta
    check(all(value > 0 for value in changes.values()), "historical order reads actually fence authorization")
    CHECKS.append("only documented authorization_fence increments: " + json.dumps(changes, sort_keys=True))
    return changes


def pg_facts(uri, *, raw_roles=False):
    with psycopg.connect(uri) as c:
        c.execute("SET TRANSACTION READ ONLY")
        verify_postgres_identity(c, uri)
        result = postgres_facts(c)
        if not raw_roles and os.environ.get("SCHEMA6_RESTORE_ROLE") == "restore_admin":
            check("restore_admin" in result["_roles"], "owned restore bootstrap role explicitly identified")
            result["_roles"].pop("restore_admin")
        return result


class DdlMonitor(monitoring.CommandListener):
    enabled = False
    violations = []

    def started(self, event):
        if self.enabled and event.command_name.lower() in {
            "create", "createindexes", "collmod", "drop", "dropdatabase", "dropindexes", "renamecollection"
        }:
            self.violations.append(event.command_name.lower())

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


MONITOR = DdlMonitor()


class LoginForm(HTMLParser):
    action = None

    def __init__(self):
        super().__init__()
        self.hidden = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action = attrs.get("action")
        if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.hidden[attrs["name"]] = attrs.get("value", "")


def oidc_login(client, actor):
    org = CONFIG["actors"][actor]["org"]
    result = client.post("/b2b/auth/login", json={"organization_id": org},
                         headers={"Origin": ORIGIN, "X-BFF-CSRF": "login"})
    check(result.status_code == 200, "OIDC begin accepted")
    auth_url = result.json()["authorization_url"]
    query = parse_qs(urlsplit(auth_url).query)
    check(query.get("code_challenge_method") == ["S256"] and query.get("nonce")
          and query.get("state") and query.get("code_challenge")
          and "client_secret" not in query and "code_verifier" not in query, "PKCE nonce and state retained")
    with httpx.Client(verify=ssl.create_default_context(cafile="/inputs/tls.crt"), trust_env=False) as provider:
        page = provider.get(auth_url)
        check(page.status_code == 200, "native Keycloak login rendered over verified TLS")
        form = LoginForm()
        form.feed(page.text)
        check(form.action and urlsplit(form.action).netloc == urlsplit(CONFIG["issuer"]).netloc,
              "native login action has exact issuer origin")
        result = provider.post(form.action, data=form.hidden | {
            "username": actor, "password": CONFIG["password"], "credentialId": "",
        })
        check(result.status_code in {302, 303}, "Keycloak credentials accepted")
        callback = result.headers["location"]
        check(callback.startswith(ORIGIN + "/b2b/auth/callback?"), "exact BFF callback destination")
        transaction = client.cookies.get(TRANSACTION_COOKIE)
        callback_response = client.get(callback)
        check(callback_response.status_code == 303, "real OIDC callback accepted")
        # Replaying the consumed browser transaction must fail.
        replay = TestClient(client.app, base_url=ORIGIN, follow_redirects=False)
        try:
            replay.cookies.set(TRANSACTION_COOKIE, transaction)
            check(replay.get(callback).status_code == 401, "OIDC transaction replay refused")
        finally:
            replay.close()
    session = client.get("/b2b/auth/session")
    check(session.status_code == 200 and session.json()["organization_id"] == org
          and session.json()["role"] == CONFIG["actors"][actor]["role"], "persistent session resolves tenant and role")
    client.headers.update({"Origin": ORIGIN, "X-BFF-CSRF": session.json()["csrf_token"]})
    return client


class Bff:
    def __init__(self, db, *, reuse=False):
        self.db = db
        self.settings = BffSettings(
            mode="b2b_business_test", origin=ORIGIN, issuer=CONFIG["issuer"],
            client_id="rollback-lab", client_secret=CONFIG["client_secret"],
            configuration_key=CONFIG["configuration_key"], oidc_ca_file="/inputs/tls.crt",
            mongo_uri=os.environ["MONGO_TEST_URI"], mongo_database=db.name,
            session_idle_seconds=1800, assistant_enabled=True,
            assistant_postgres_uri=os.environ["POSTGRES_TEST_URI"],
            assistant_ollama_url="https://keycloak:8443",
            # No generation requested: current low-stock routing and stored-turn replay
            # must work without calling a model. No real Ollama is accessed.

        )
        self.app = create_app(self.settings)
        self.stack = ExitStack()
        self.clients = {}
        self.reuse = reuse

    def __enter__(self):
        self.before = mongo_facts(self.db)
        MONITOR.enabled = True
        self.root = self.stack.enter_context(TestClient(self.app, base_url=ORIGIN, follow_redirects=False))
        check(self.app.state.bff_business is not None, "real BFF lifespan composes existing schema")
        check(mongo_facts(self.db) == self.before, "BFF startup creates no data or schema")
        if self.reuse:
            c = self.browser("a1", login=False)
            old = load("session")
            c.cookies.set(SESSION_COOKIE, old["cookie"])
            response = c.get("/b2b/auth/session")
            check(response.status_code == 200 and response.json()["user_id"] == "a1",
                  "session cookie survives application image change")
            c.headers.update({"Origin": ORIGIN, "X-BFF-CSRF": response.json()["csrf_token"]})
        return self

    def browser(self, actor="a1", login=True):
        if actor not in self.clients:
            c = TestClient(self.app, base_url=ORIGIN, follow_redirects=False)
            self.stack.callback(c.close)
            self.clients[actor] = c
            if login:
                oidc_login(c, actor)
        return self.clients[actor]

    def remember_session(self):
        c = self.browser()
        save("session", {"cookie": c.cookies.get(SESSION_COOKIE)})

    def __exit__(self, *exc):
        self.stack.close()
        MONITOR.enabled = False
        check(not MONITOR.violations, "no implicit Mongo DDL during BFF lifecycle or requests")


def response(c, method, path, *, status=200, **kwargs):
    r = getattr(c, method)(path, **kwargs)
    check(r.status_code == status, method.upper() + " " + path.split("?")[0] + " expected " + str(status) + " actual " + str(r.status_code))
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None


def prepare(c, op, buyer="customer-org-a"):
    return response(c, "post", "/b2b/api/orders/prepare", json={
        "operation_id": op, "customer_id": buyer, "lines": [{"sku": "DRILL-001", "quantity": 1}],
    })


def confirm(c, order, op):
    return response(c, "post", "/b2b/api/orders/" + order["order_id"] + "/confirm", json={
        "operation_id": op, "preparation_digest": order["preparation_digest"], "confirmed": True,
    })


def cancel(c, order, op):
    return response(c, "post", "/b2b/api/orders/" + order["order_id"] + "/cancel", json={"operation_id": op})


def inventory(db, org="org-a"):
    d = db.b2b_products.find_one({"organization_id": org, "sku": "DRILL-001"})
    return d["quantity"], d["reserved"], d["revision"]


def seed(db):
    check(len(registry()) == 4, "unmodified v0.2.3 registry has exactly four migrations")
    check(not db.list_collection_names(), "seed target is empty synthetic database")
    MigrationRunner(db).apply(confirmation=db.name)
    MigrationRunner(db).require_version(4)
    initialize_b2b_indexes(db)
    MongoBffStore(db).initialize()  # explicit fixture provisioning, never BFF startup
    now = datetime.now(UTC)
    for org in ("org-a", "org-b"):
        db.b2b_organizations.insert_one({"organization_id": org, "name": org, "status": "active",
                                        "created_at": now, "updated_at": now})
        for sku, status in (("DRILL-001", "active"), ("SAW-001", "active"), ("INACTIVE-001", "inactive")):
            db.b2b_products.insert_one({
                "_id": ObjectId(), "organization_id": org, "sku": sku, "name": "Synthetic tool",
                "description": "Qualification fixture", "category": "drill", "price_minor": 4900,
                "currency": "EUR", "status": status, "attributes": ["electric"],
                "quantity": 3 if sku == "SAW-001" else 20, "reserved": 0, "revision": 1,
            })
    for actor, item in CONFIG["actors"].items():
        db.b2b_users.insert_one({"user_id": actor, "external_identity": {
            "issuer": CONFIG["issuer"], "subject": item["subject"]}, "status": "active", "created_at": now, "updated_at": now})
        db.b2b_memberships.insert_one({"membership_id": "m-" + actor, "user_id": actor,
            "organization_id": item["org"], "role": item["role"], "status": "active", "created_at": now, "updated_at": now})
        db.b2b_buyers.insert_one({"association_id": "buyer-" + actor, "organization_id": item["org"],
            "user_id": actor, "customer_id": "customer-" + item["org"], "status": "active", "revision": 1})
    pg_setup()
    with Bff(db) as b:
        c = b.browser()
        orders = {name: prepare(c, "seed-" + name) for name in ("prepared", "reserved", "archived", "restored")}
        for name in ("reserved", "archived", "restored"):
            confirm(c, orders[name], "seed-confirm-" + name)
        for name in ("archived", "restored"):
            cancel(c, orders[name], "seed-cancel-" + name)
        foreign = prepare(b.browser("b1"), "seed-foreign", "customer-org-b")
        confirm(b.browser("b1"), foreign, "seed-confirm-foreign")
        orders["foreign"] = foreign
        product = response(c, "get", "/b2b/api/products/by-sku?sku=DRILL-001")
        response(b.browser("admin-a"), "post", "/b2b/api/products/" + product["product_id"] + "/adjustments",
                 json={"operation_id": "seed-adjust", "quantity": 25, "expected_revision": product["revision"],
                       "reason": "Synthetic inventory"})
        save("orders", orders)
        pg_business(b, seed=True)
        b.remember_session()
    save("v4", mongo_facts(db))
    save("identities", identities(db))


def pg_setup():
    from stockpilot.infrastructure.persistence.postgres.tenant_migrations import PostgresTenantMigrations
    uri = validate_postgres_test_uri(os.environ, Settings())
    with psycopg.connect(uri) as c:
        verify_postgres_identity(c, uri)
        runner = PostgresTenantMigrations(uri)
        database, identity = runner.target(c)
    runner.apply(database=database, system_identifier=identity)
    # Extra synthetic objects exercise dump/restore semantics missed by row counts.
    with psycopg.connect(uri) as c:
        verify_postgres_identity(c, uri)
        c.execute("CREATE SCHEMA recovery_qualification")
        c.execute("CREATE TABLE recovery_qualification.live_columns (id integer PRIMARY KEY, "
                  "retired text, amount numeric(38,12) NOT NULL CHECK(amount>=0), payload bytea)")
        c.execute("ALTER TABLE recovery_qualification.live_columns DROP COLUMN retired")
        c.execute("INSERT INTO recovery_qualification.live_columns VALUES "
                  "(1,12345678901234567890.123456789012,decode('00ff','hex'))")
        c.execute("CREATE SEQUENCE recovery_qualification.stateful START 5 INCREMENT 3")
        c.execute("SELECT setval('recovery_qualification.stateful',53,false)")
        c.execute("CREATE ROLE qualification_reader NOLOGIN")
        c.execute("GRANT USAGE ON SCHEMA recovery_qualification TO qualification_reader")
        c.execute("GRANT SELECT ON recovery_qualification.live_columns TO qualification_reader")



def pg_business(b, *, seed=False):
    from stockpilot.infrastructure.identity.bff_tenant_context import BffTenantRequest
    from stockpilot.infrastructure.persistence.postgres.tenant_knowledge import PgTenantKnowledge
    from stockpilot.infrastructure.persistence.postgres.tenant_checkpointer import PgTenantConversations
    from stockpilot.infrastructure.persistence.postgres.tenant_migrations import PostgresTenantMigrations
    from stockpilot.domain.entities.tenant_knowledge import IngestTenantDocument
    from stockpilot.application.ports.tenant_knowledge import TenantDataNotFound
    from langgraph.checkpoint.base import empty_checkpoint
    uri = os.environ["POSTGRES_TEST_URI"]
    check(len(PostgresTenantMigrations(uri).status()) == 2, "PostgreSQL migration history accepted")
    auth = b.app.state.bff_business.authorization
    vector = [1.0] + [0.0] * 767

    class Embedding:
        async def embed(self, text):
            return vector

    repo = PgTenantKnowledge(uri, auth, Embedding(), model="synthetic-768", version="v1")
    convs = PgTenantConversations(uri, auth)

    def actor(name):
        request = BffTenantRequest(b.browser(name).cookies.get(SESSION_COOKIE))
        return request, auth.resolve_context(request)

    if seed:
        saved = {}
        for user, admin in (("a1", "admin-a"), ("b1", "admin-b")):
            req, ctx = actor(admin)
            asyncio.run(repo.ingest(req, ctx, IngestTenantDocument(
                "seed-rag", "same-document", "Synthetic", ctx.organization_id + " document",
                "synthetic-768", "v1", 0)))
            req, ctx = actor(user)
            conversation = convs.open(req, ctx, "same-thread")
            saver = convs.bind(req, ctx, conversation.conversation_id)
            cp = empty_checkpoint()
            cp["channel_values"] = {"message": ctx.organization_id + " checkpoint"}
            saver.put(saver.config(), cp, {"source": "input", "step": 0}, {})
            saved[user] = conversation.conversation_id
        save("conversations", saved)
    for name in ("a1", "b1"):
        req, ctx = actor(name)
        rows = repo.search(req, ctx, vector, model="synthetic-768", version="v1")
        check(len(rows) == 1 and rows[0].organization_id == ctx.organization_id
              and rows[0].content == ctx.organization_id + " document", "RAG remains tenant scoped")
        saver = convs.bind(req, ctx, load("conversations")[name])
        check(saver.get_tuple(saver.config()).checkpoint["channel_values"]["message"] ==
              ctx.organization_id + " checkpoint", "LangGraph checkpoint readable across versions")
    req, ctx = actor("b1")
    try:
        convs.bind(req, ctx, load("conversations")["a1"])
    except TenantDataNotFound:
        CHECKS.append("foreign conversation refused")
    else:
        raise AssertionError("foreign conversation leaked")


def upgrade(db):
    check(len(registry()) == 6, "current registry has six migrations")
    before = mongo_facts(db)
    check(before == load("v4"), "v4 data unchanged between stopped application phases")
    old_history = list(db[HISTORY].find().sort("_id", 1))
    for version in (5, 6):
        runner = MigrationRunner(db, registry()[:version])
        check([m.version for m in runner.plan()] == [version], "explicit ordered migration " + str(version))
        runner.apply(confirmation=db.name)
        runner.require_version(version)
    after = mongo_facts(db)
    check(set(after) - set(before) == NEW, "exactly five additive collections")
    check({k: after[k] for k in before if k != HISTORY} ==
          {k: before[k] for k in before if k != HISTORY}, "all existing documents options and indexes unchanged by upgrade")
    check(list(db[HISTORY].find().sort("_id", 1))[:4] == old_history, "original migration history preserved byte logically")
    MigrationRunner(db).apply(confirmation=db.name)
    check(mongo_facts(db) == after, "reapplication changes no data structure or history")
    with Bff(db, reuse=True) as b:
        c, admin = b.browser(), b.browser("admin-a")
        orders = load("orders")
        for name in ("archived", "restored"):
            path = "/b2b/api/orders/" + orders[name]["order_id"]
            response(c, "post", path + "/archive", json={"operation_id": "archive-" + name, "confirmed": True})
            response(b.browser("b1"), "post", path + "/archive", status=404,
                     json={"operation_id": "foreign-archive-" + name, "confirmed": True})
        response(c, "post", "/b2b/api/orders/" + orders["restored"]["order_id"] + "/restore",
                 json={"operation_id": "restore", "confirmed": True})
        root = "/b2b/api/contact/requests"
        body = {"operation_id": "contact", "subject": "Synthetic question", "category": "access", "description": "Fixture"}
        receipt = response(c, "post", root, json=body)
        check(response(c, "post", root, json=body) == receipt, "contact creation idempotent")
        path = root + "/" + receipt["request_id"]
        response(b.browser("b1"), "get", path, status=404)
        response(b.browser("a2"), "post", path + "/replies", status=403,
                 json={"operation_id": "denied", "body": "No grant"})
        response(admin, "post", path + "/replies", json={"operation_id": "reply", "body": "Synthetic answer"})
        response(admin, "post", path + "/status", json={"operation_id": "resolved", "status": "resolved"})
        save("contact", {"receipt": receipt, "body": body})
        assistant_root = "/b2b/api/assistant/conversations"
        cid = response(c, "post", assistant_root, json={"thread_id": "v024-catalogue"})["conversation_id"]
        message = {"message_id": "v024-catalogue", "message": "Quels produits ont moins de 5 unités disponibles ?",
                   "read_only": True}
        turn = response(c, "post", assistant_root + "/" + cid + "/messages", json=message)
        check(turn["reply"]["can_browse_products"] is True and turn["reply"]["products"],
              "current assistant persists enriched catalogue turn without a model call")
        save("assistant-turn", {"conversation_id": cid, "body": message, "turn": turn})
        b.remember_session()
    save("v6-new", {k: v for k, v in mongo_facts(db).items() if k in NEW})
    save("v6-history", mongo_facts(db)[HISTORY])
    save("v6-existing-orders", [canonical(d) for d in db.b2b_orders.find({}).sort("_id", 1)])
    save("v6-pg", pg_facts(os.environ["POSTGRES_TEST_URI"]))


def read_business(b):
    c = b.browser()
    response(c, "get", "/b2b/api/products")
    product = response(c, "get", "/b2b/api/products/by-sku?sku=DRILL-001")
    response(c, "get", "/b2b/api/products/" + product["product_id"] + "/stock")
    response(c, "get", "/b2b/api/orders")
    response(c, "get", "/b2b/api/stock/movements")
    response(b.browser("admin-a"), "get", "/b2b/api/imports")
    response(c, "get", "/b2b/api/orders/operations/confirm/seed-confirm-reserved")
    response(b.browser("admin-a"), "get", "/b2b/api/stock/operations/adjust/seed-adjust")
    response(b.browser("b1"), "get", "/b2b/api/products/" + product["product_id"], status=404)
    response(b.browser("a3"), "get", "/b2b/api/orders/" + load("orders")["reserved"]["order_id"], status=404)
    response(c, "post", "/b2b/api/products/" + product["product_id"] + "/adjustments", status=403,
             json={"operation_id": "denied", "quantity": 26, "expected_revision": product["revision"], "reason": "Denied"})
    response(b.browser("readonly"), "post", "/b2b/api/orders/prepare", status=403,
             json={"operation_id": "denied-ro", "customer_id": "customer-org-a", "lines": [{"sku": "DRILL-001", "quantity": 1}]})
    response(c, "post", "/b2b/api/orders/prepare", status=403, headers={"X-BFF-CSRF": "wrong"},
             json={"operation_id": "denied-csrf", "customer_id": "customer-org-a", "lines": [{"sku": "DRILL-001", "quantity": 1}]})
    response(c, "post", "/b2b/api/orders/prepare", status=403, headers={"Origin": "https://foreign.invalid"},
             json={"operation_id": "denied-origin", "customer_id": "customer-org-a",
                   "lines": [{"sku": "DRILL-001", "quantity": 1}]})
    cross_origin = c.get("/b2b/api/products", headers={"Origin": "https://foreign.invalid"})
    check(cross_origin.status_code == 200 and "access-control-allow-origin" not in cross_origin.headers
          and all(p["organization_id"] == "org-a" for p in cross_origin.json()["items"]),
          "historical read contract grants no CORS access or tenant switch")
    pg_business(b)


def rollback(db):
    MigrationRunner(db).require_version(6)
    prefix = "restored-rollback-" if os.environ.get("SCHEMA6_RESTORE_ROLE") else "rollback-"
    before, stock_before = mongo_facts(db), inventory(db)
    products_before = list(db.b2b_products.find({}))
    operations_before = list(db.b2b_order_operations.find({}))
    orders_before = list(db.b2b_orders.find({}))
    with Bff(db, reuse=True) as b:
        read_business(b)
        c = b.browser()
        stored = load("assistant-turn")
        assistant_path = "/b2b/api/assistant/conversations/" + stored["conversation_id"]
        history = response(c, "get", assistant_path)
        check(history["items"][-1]["reply"]["products"] == stored["turn"]["reply"]["products"],
              "v0.2.3 reads v0.2.4 enriched assistant turn without discarding stored data")
        replay = response(c, "post", assistant_path + "/messages", json=stored["body"])
        check(replay["reply"]["products"] == stored["turn"]["reply"]["products"]
              and "can_browse_products" not in replay["reply"],
              "old completed-turn replay remains compatible without advertising new capability")
        response(b.browser("b1"), "get", assistant_path, status=404)
        response(c, "get", assistant_path + "/turns/v024-catalogue/products", status=404)
        for method, path, payload in (
            ("get", "/b2b/api/contact/requests", None),
            ("post", "/b2b/api/contact/requests", {"operation_id": "absent"}),
            ("post", "/b2b/api/orders/" + load("orders")["archived"]["order_id"] + "/archive",
             {"operation_id": "absent", "confirmed": True}),
            ("post", "/b2b/api/orders/" + load("orders")["restored"]["order_id"] + "/restore",
             {"operation_id": "absent", "confirmed": True}),
        ):
            response(c, method, path, status=404, **({"json": payload} if payload is not None else {}))
        order = prepare(c, prefix + "prepare")
        check(inventory(db) == stock_before, "preparation reserves nothing")
        check(prepare(c, prefix + "prepare") == order, "preparation replay exact")
        path = "/b2b/api/orders/" + order["order_id"] + "/confirm"
        response(c, "post", path, status=422, json={"operation_id": "missing-confirmation", "preparation_digest": order["preparation_digest"]})
        response(c, "post", path, status=422, json={"operation_id": "false-confirmation", "preparation_digest": order["preparation_digest"], "confirmed": False})
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: confirm(c, order, prefix + "confirm"), range(2)))
        check(results[0] == results[1] and inventory(db)[1] == stock_before[1] + 1,
              "concurrent explicit confirmation reserves atomically once")
        check(confirm(c, order, prefix + "confirm") == results[0], "confirmed receipt replay exact")
        cancelled = cancel(c, order, prefix + "cancel")
        check(cancel(c, order, prefix + "cancel") == cancelled and inventory(db)[:2] == stock_before[:2],
              "cancellation replay releases only its reservation")
        b.remember_session()
    after = mongo_facts(db)
    check(set(after) == set(before), "candidate creates no collection")
    for row in operations_before:
        check(db.b2b_order_operations.find_one({"_id": row["_id"]}) == row, "preexisting ORDER receipt preserved")
    for row in orders_before:
        check(db.b2b_orders.find_one({"_id": row["_id"]}) == row, "every prior order preserved")
    for row in products_before:
        expected = dict(row)
        if row["organization_id"] == "org-a" and row["sku"] == "DRILL-001":
            expected["revision"] += 2
        check(db.b2b_products.find_one({"_id": row["_id"]}) == expected,
              "product physical/reserved values preserved; only tested revision increments")
    for name in ("b2b_stock_movements", "b2b_stock_operations", "b2b_imports", "b2b_product_archives", LOCKS):
        check(after[name] == before[name], "non-ORDER data untouched: " + name)
    check({k: after[k] for k in NEW} == load("v6-new"), "all five v0.2.4 collections unchanged by rollback")
    check(after[HISTORY] == load("v6-history"), "v6 history untouched by candidate")
    check(identities(db) == load("identities"), "accounts roles memberships and buyer grants preserved")
    check(all(canonical(db.b2b_orders.find_one({"_id": json_util.loads(d)["_id"]})) == d
              for d in load("v6-existing-orders")), "every preexisting order preserved exactly")
    check(pg_facts(os.environ["POSTGRES_TEST_URI"]) == load("v6-pg"), "all PG data and structures unchanged by rollback reads")
    for name in before:
        check(after[name]["options"] == before[name]["options"] and after[name]["indexes"] == before[name]["indexes"],
              "collection structure retained: " + name)
    check(after["b2b_orders"]["count"] == before["b2b_orders"]["count"] + 1
          and after["b2b_order_operations"]["count"] == before["b2b_order_operations"]["count"] + 3,
          "only the requested synthetic ORDER and three durable receipts added")
    save("rollback-facts", after)


def return_current(db):
    MigrationRunner(db).require_version(6)
    with Bff(db, reuse=True) as b:
        read_business(b)
        c = b.browser()
        page = response(c, "get", "/b2b/api/orders?archived=true&status=cancelled")
        check([o["order_id"] for o in page["items"]] == [load("orders")["archived"]["order_id"]],
              "personal archive reappears after returning to current code")
        stored = load("assistant-turn")
        assistant_path = "/b2b/api/assistant/conversations/" + stored["conversation_id"]
        history = response(c, "get", assistant_path)
        check(history["items"][-1]["reply"]["can_browse_products"] is True
              and history["items"][-1]["reply"]["products"] == stored["turn"]["reply"]["products"],
              "current assistant regains enriched catalogue capability with original cards")
        response(c, "get", assistant_path + "/turns/v024-catalogue/products")
        root = "/b2b/api/contact/requests"
        contact = load("contact")
        path = root + "/" + contact["receipt"]["request_id"]
        check(response(c, "get", path)["status"] == "resolved", "contact status retained")
        check(response(c, "get", path + "/replies")["items"][0]["body"] == "Synthetic answer", "admin reply retained")
        check(response(c, "post", root, json=contact["body"]) == contact["receipt"], "contact receipt survives roundtrip")
        response(c, "post", "/b2b/api/orders/" + load("orders")["restored"]["order_id"] + "/archive",
                 json={"operation_id": "archive-restored", "confirmed": True})
        check(response(c, "get", "/b2b/api/orders?archived=true")["total"] == 1,
              "old archive replay does not revert subsequent restore")
        b.remember_session()
    facts = mongo_facts(db)
    check({k: facts[k] for k in NEW} == load("v6-new"), "all archive contact receipts preserved after roundtrip")
    check(facts[HISTORY] == load("v6-history"), "no migration conflict after return")
    check(identities(db) == load("identities"), "identity semantics retained after return")
    check(pg_facts(os.environ["POSTGRES_TEST_URI"]) == load("v6-pg"), "PG data intact after return")


def negative(db):
    """Each corruption gets its own newly created disposable DB; source untouched."""
    from copy import deepcopy
    before = mongo_facts(db)
    cases = ("unknown-v7", "gap", "checksum", "incomplete", "index", "validator",
             "pending-stock")
    for case in cases:
        name = "stockpilot_b2b_test_" + uuid4().hex
        target = db.client[name]
        check(not target.list_collection_names(), "negative target independent and empty")
        try:
            for item in db.list_collections():
                target.create_collection(item["name"], **item.get("options", {}))
                docs = list(db[item["name"]].find())
                if docs:
                    target[item["name"]].insert_many(deepcopy(docs))
                for idx in db[item["name"]].list_indexes():
                    if idx["name"] != "_id_":
                        opts = {k: v for k, v in idx.items() if k not in {"key", "v", "ns"}}
                        target[item["name"]].create_index(list(idx["key"].items()), **opts)
            if case == "unknown-v7":
                row = target[HISTORY].find_one({"_id": 6}); row["_id"] = 7
                target[HISTORY].insert_one(row)
            elif case == "gap":
                target[HISTORY].delete_one({"_id": 5})
            elif case == "checksum":
                target[HISTORY].update_one({"_id": 6}, {"$set": {"checksum": "0" * 64}})
            elif case == "incomplete":
                target[HISTORY].update_one({"_id": 6}, {"$set": {"status": "started"}})
            elif case == "index":
                target.b2b_contact_requests.create_index("subject", name="unexpected")
            elif case == "validator":
                target.command({"collMod": "b2b_contact_requests", "validationLevel": "off"})
            elif case.startswith("pending-"):
                collection = "b2b_stock_operations"
                row = target[collection].find_one()
                check(row is not None, "pending fixture has an existing synthetic receipt")
                target[collection].update_one({"_id": row["_id"]}, {"$set": {"status": "pending"}})
            corrupted = mongo_facts(target)
            if not case.startswith("pending-"):
                try:
                    MigrationRunner(target).require_version(6)
                except MigrationError:
                    CHECKS.append("fail closed: " + case)
                else:
                    raise AssertionError("unsafe schema accepted: " + case)
            try:
                mongo_preconditions(target)
            except ContractError:
                CHECKS.append("recovery contract refuses: " + case)
            else:
                raise AssertionError("unsafe recovery capture accepted: " + case)
            check(mongo_facts(target) == corrupted, "failed validation never repairs: " + case)
        finally:
            # This name is minted above, on the attested private server only.
            db.client.drop_database(name)
    for target, confirmation in ((db, None), (db, "wrong"),
                                  (db.client["stockpilot"], "stockpilot")):
        try:
            MigrationRunner(target).apply(confirmation=confirmation)
        except MigrationError:
            CHECKS.append("protected/unconfirmed apply refused")
        else:
            raise AssertionError("unsafe apply accepted")
    check(mongo_facts(db) == before and "stockpilot" not in db.client.list_database_names(),
          "all negative cases leave source and protected name untouched")


def main():
    global CONFIG
    logging.disable(logging.CRITICAL)
    CONFIG = json.loads(Path("/inputs/config.json").read_text())
    monitoring.register(MONITOR)
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("wait-keycloak", "seed-v4", "upgrade", "old-refusal", "rollback",
                                          "return", "negative", "snapshot", "serve-browser", "browser-before", "browser-after"))
    phase = parser.parse_args().phase
    if phase == "wait-keycloak":
        for _ in range(150):
            try:
                r = httpx.get(CONFIG["issuer"] + "/.well-known/openid-configuration",
                              verify=ssl.create_default_context(cafile="/inputs/tls.crt"),
                              trust_env=False, timeout=3)
                if r.status_code == 200 and r.json()["issuer"] == CONFIG["issuer"]:
                    check(True, "Keycloak discovery exact and TLS validated")
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise AssertionError("Keycloak discovery deadline")
    else:
        with mongo_test_client() as client:
            db = client[CONFIG["database"]]
            if phase == "serve-browser":
                import uvicorn
                server = Bff(db)
                # Real HTTPS upstream: no forwarded-header exception or TLS bypass.
                uvicorn.run(server.app, host="0.0.0.0", port=8000,
                    ssl_keyfile="/inputs/tls.key", ssl_certfile="/inputs/tls.crt",
                    proxy_headers=False, access_log=False, log_level="critical")
            elif phase in {"browser-before", "browser-after"}:
                facts = browser_business_facts(db)
                if phase == "browser-before":
                    save("browser-business", facts)
                else:
                    compare_browser_business(load("browser-business"), facts)
            elif phase == "old-refusal":
                before = mongo_facts(db)
                try:
                    MigrationRunner(db).require_version(4)
                except MigrationError:
                    check(mongo_facts(db) == before, "unchanged v0.2.3 refuses v6 without effects")
                else:
                    raise AssertionError("old binary unexpectedly accepts v6")
            elif phase == "snapshot":
                save("snapshot", {"mongo": mongo_facts(db),
                     "application": pg_facts(os.environ["POSTGRES_TEST_URI"], raw_roles=True),
                    "keycloak": pg_facts(CONFIG["keycloak_pg_uri"], raw_roles=True),
                    "schema": mongo_preconditions(db), "excluded_ephemeral": []})
                with psycopg.connect(os.environ["POSTGRES_TEST_URI"]) as connection:
                    connection.execute("SET TRANSACTION READ ONLY")
                    dropped = connection.execute("SELECT count(*) FROM pg_attribute WHERE "
                        "attrelid='recovery_qualification.live_columns'::regclass AND attisdropped").fetchone()[0]
                save("physical-columns", {"dropped_column_slots": dropped})
                CHECKS.append("dynamic Mongo and both PostgreSQL inventories captured")
            else:
                {"seed-v4": seed, "upgrade": upgrade, "rollback": rollback,
                 "return": return_current, "negative": negative}[phase](db)
    report = {"phase": phase, "success": True, "checks": CHECKS}
    save("result-" + phase, report)
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # No raw driver/HTTP errors, credentials, responses, cookies or traceback locals.
        import traceback
        frames = traceback.extract_tb(error.__traceback__)
        print(json.dumps({"success": False, "error": type(error).__name__,
                          "check": str(error) if isinstance(error, AssertionError) else "dependency failure",
                          "frames": [{"file": Path(f.filename).name, "line": f.lineno} for f in frames[-6:]]}))
        raise SystemExit(1) from None
