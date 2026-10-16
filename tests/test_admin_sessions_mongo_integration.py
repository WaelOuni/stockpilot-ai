"""Real shared session behavior on a V13.1 guarded, disposable Mongo database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier
import time
import re
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from integration_targets import mongo_test_client
from stockpilot.application.ports.admin_session_store import LoginAttemptsExceeded, SessionCapacityReached
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.admin_sessions import MongoAdminSessions
from stockpilot.presentation.api.admin_auth import password_verifier, router


pytestmark = pytest.mark.integration
PASSWORD = "synthetic-only-operator-password"
VERIFIER = password_verifier(PASSWORD)
ORIGIN = "https://admin.example.test"
CONFIGURATION = "synthetic-configuration-fingerprint"


@pytest.fixture
def stores():
    # Guard verifies the effective server before any collection/index is created.
    client = mongo_test_client()
    name = "stockpilot_admin_test_" + uuid4().hex
    assert re.fullmatch(r"stockpilot_admin_test_[0-9a-f]{32}", name)
    database = client[name]
    clock = [time.time()]
    first = MongoAdminSessions(database, clock=lambda: clock[0])
    second = MongoAdminSessions(database, clock=lambda: clock[0])
    try:
        first.initialize()
        second.initialize()
        yield first, second, clock, database
    finally:
        client.drop_database(name)
        client.close()


def create(store, previous="", *, capacity=100, ttl=1800):
    return store.create("synthetic-operator", previous, ttl, capacity, CONFIGURATION)


def test_shared_lookup_restart_revocation_and_hash_at_rest(stores):
    first, second, _, database = stores
    token, session = create(first)
    assert second.get(token, CONFIGURATION) == session
    restarted = MongoAdminSessions(database)
    restarted.initialize()
    assert restarted.get(token, CONFIGURATION) == session
    record = database.operator_sessions.find_one({})
    assert record["_id"] == first.token_hash(token)
    assert token not in str(record)
    assert set(record) == {"_id", "username", "csrf_token", "configuration", "expires_at"}
    assert isinstance(record["expires_at"], datetime)
    assert any(index.get("expireAfterSeconds") == 0 for index in database.operator_sessions.list_indexes())
    second.revoke(token, CONFIGURATION)
    assert first.get(token, CONFIGURATION) is None
    assert restarted.get(token, CONFIGURATION) is None


def test_expiry_is_explicit_and_does_not_depend_on_ttl_cleanup(stores):
    first, second, clock, database = stores
    token, _ = create(first, ttl=3600)
    clock[0] += 3600
    assert second.get(token, CONFIGURATION) is None
    # Real UTC expiry is still an hour away; the test advances only the app clock.
    assert database.operator_sessions.count_documents({}) == 1


def test_configuration_binding_never_clears_another_worker(stores):
    first, second, _, database = stores
    token, session = create(first)
    second.configure("different-configuration")
    assert second.get(token, "different-configuration") is None
    assert first.get(token, CONFIGURATION) == session
    second.revoke(token, "different-configuration")
    assert database.operator_sessions.count_documents({}) == 1
    # Fingerprints deny access under changed config; they do not erase records.
    assert second.get(token, CONFIGURATION) == session
    first.revoke(token, CONFIGURATION)
    second.configure("different-configuration")
    second.configure(CONFIGURATION)
    assert second.get(token, CONFIGURATION) is None


def test_rotation_and_capacity_are_shared_and_old_csrf_is_disarmed(stores):
    first, second, _, database = stores
    previous, old_session = create(first, capacity=1)
    with pytest.raises(SessionCapacityReached):
        create(second, capacity=1)
    token, session = create(second, previous, capacity=1)
    assert token != previous and session.csrf_token != old_session.csrf_token
    assert first.get(previous, CONFIGURATION) is None
    assert first.get(token, CONFIGURATION) == session
    assert database.operator_sessions.count_documents({}) == 1


def test_parallel_capacity_admits_only_the_configured_limit(stores):
    first, second, _, database = stores
    barrier = Barrier(8)

    def admission(index):
        barrier.wait(timeout=10)
        try:
            return create(first if index % 2 else second, capacity=2)[0]
        except SessionCapacityReached:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(admission, range(8)))
    assert len([token for token in results if token]) == 2
    assert database.operator_sessions.count_documents({}) == 2


def test_parallel_rotation_at_capacity_never_leaves_two_valid_sessions(stores):
    first, second, _, database = stores
    previous, _ = create(first, capacity=1)
    barrier = Barrier(2)

    def rotate(store):
        barrier.wait(timeout=10)
        try:
            return create(store, previous, capacity=1)[0]
        except SessionCapacityReached:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(rotate, (first, second)))
    assert len([token for token in results if token]) == 1
    assert first.get(previous, CONFIGURATION) is None
    assert database.operator_sessions.count_documents({}) == 1


def test_login_throttle_is_shared_across_workers_and_restart(stores):
    first, second, clock, database = stores
    for store in (first, first, second, second, first):
        store.attempt("synthetic-address")
    restarted = MongoAdminSessions(database, clock=lambda: clock[0])
    restarted.initialize()
    with pytest.raises(LoginAttemptsExceeded):
        restarted.attempt("synthetic-address")
    record = database.operator_session_guards.find_one({"_id": "attempts"})
    assert "synthetic-address" not in str(record)
    assert len(record["attempts"]) == 5
    clock[0] += 60
    restarted.attempt("synthetic-address")
    assert len(database.operator_session_guards.find_one({"_id": "attempts"})["attempts"]) == 1


def test_global_throttle_is_bounded_and_shared(stores):
    first, second, _, database = stores
    for index in range(50):
        (first if index % 2 else second).attempt(str(index))
    with pytest.raises(LoginAttemptsExceeded):
        second.attempt("new-address")
    assert len(database.operator_session_guards.find_one({"_id": "attempts"})["attempts"]) == 50


def test_parallel_login_attempts_do_not_exceed_address_limit(stores):
    first, second, _, _ = stores
    barrier = Barrier(8)

    def attempt(index):
        barrier.wait(timeout=10)
        try:
            (first if index % 2 else second).attempt("same-address")
            return True
        except LoginAttemptsExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(8))) == 5


def make_client(store):
    app = FastAPI()
    app.include_router(router)
    app.state.admin_sessions = store
    app.state.settings = Settings(
        admin_username="synthetic-operator", admin_password_verifier=VERIFIER,
        admin_trusted_origin=ORIGIN, admin_session_store="mongo", _env_file=None,
    )
    return TestClient(app, base_url=ORIGIN)


def test_http_login_me_logout_and_csrf_work_across_independent_apps(stores):
    first, second, _, database = stores
    client = make_client(first)
    other = make_client(second)
    response = client.post("/backoffice/session/login", headers={"Origin": ORIGIN, "X-Admin-CSRF": "login"},
                           json={"username": "synthetic-operator", "password": PASSWORD})
    assert response.status_code == 200
    token = client.cookies.get("__Host-stockpilot_admin")
    cookie = {"Cookie": "__Host-stockpilot_admin=" + token}
    assert other.get("/backoffice/session/me", headers=cookie).json() == response.json()
    headers = {**cookie, "Origin": ORIGIN, "X-Admin-CSRF": "wrong"}
    assert other.post("/backoffice/session/logout", headers=headers).status_code == 403
    assert database.operator_sessions.count_documents({}) == 1
    headers["X-Admin-CSRF"] = response.json()["csrf_token"]
    assert other.post("/backoffice/session/logout", headers=headers).status_code == 204
    assert client.get("/backoffice/session/me").status_code == 401
    assert database.operator_sessions.count_documents({}) == 0
