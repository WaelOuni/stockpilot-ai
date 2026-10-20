"""Dedicated guarded Mongo replica set only; no application database fallback."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from integration_targets import mongo_test_client
from pymongo.errors import DuplicateKeyError

from stockpilot.application.ports.bff_security import (
    BffSession,
    BffUnavailable,
    LoginTransaction,
)
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
    MongoIdentityRepository,
    MongoMembershipRepository,
    MongoOrganizationRepository,
    initialize_b2b_indexes,
)
from stockpilot.infrastructure.persistence.mongodb.bff_store import MongoBffStore

pytestmark = pytest.mark.integration
IDENTITY = ExternalIdentity("https://issuer.example/realms/test", "fixture-subject")


@pytest.fixture
def owned_database():
    if not os.environ.get("MONGO_TEST_URI"):
        pytest.skip("Dedicated MONGO_TEST_URI required")
    client = mongo_test_client()
    name = "stockpilot_b2b_test_" + uuid4().hex
    try:
        assert client.admin.command("hello").get("setName")
        db = client[name]
        initialize_b2b_indexes(db)
        MongoBffStore(db).initialize()
        yield db
    finally:
        client.drop_database(name)
        client.close()


def make_session(now, key="new-hash"):
    return BffSession(
        key,
        IDENTITY,
        "user",
        "org",
        "csrf",
        now,
        now + timedelta(hours=8),
        now + timedelta(minutes=30),
        "configuration",
    )


def make_transaction(now):
    return LoginTransaction(
        "state",
        "browser",
        "nonce",
        "verifier",
        "org",
        now + timedelta(minutes=5),
        "configuration",
    )


def test_one_use_across_workers(owned_database):
    now = datetime.now(UTC).replace(microsecond=0)
    stores = [MongoBffStore(owned_database), MongoBffStore(owned_database)]
    stores[0].put_transaction(make_transaction(now))
    assert stores[1].consume_transaction("state", "wrong", "configuration", now) is None
    assert stores[1].consume_transaction("state", "browser", "wrong", now) is None
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda s: s.consume_transaction(
                    "state", "browser", "configuration", now
                ),
                stores,
            )
        )
    assert sum(value is not None for value in outcomes) == 1


def test_transaction_expiry_without_ttl(owned_database):
    now = datetime.now(UTC).replace(microsecond=0)
    store = MongoBffStore(owned_database)
    value = make_transaction(now)
    store.put_transaction(value)
    assert (
        store.consume_transaction("state", "browser", "configuration", value.expires_at)
        is None
    )
    assert owned_database.bff_login_transactions.count_documents({}) == 1


def test_shared_session_revoke_no_resurrection(owned_database):
    now = datetime.now(UTC).replace(microsecond=0)
    first, second = MongoBffStore(owned_database), MongoBffStore(owned_database)
    first.rotate_session(make_session(now), None)
    assert second.get_session("new-hash", "wrong", now, 1800) is None
    assert second.get_session("new-hash", "configuration", now, 1800).created_at.tzinfo
    first.revoke_session("new-hash", "wrong")
    assert second.get_session("new-hash", "configuration", now, 1800)
    first.revoke_session("new-hash", "configuration")
    assert second.get_session("new-hash", "configuration", now, 1800) is None
    assert owned_database.bff_sessions.count_documents({}) == 0


@pytest.mark.parametrize("kind", ["absolute", "idle"])
def test_expiry_strict_and_idle_capped(owned_database, kind):
    now = datetime.now(UTC).replace(microsecond=0)
    store = MongoBffStore(owned_database)
    value = replace(
        make_session(now), **{kind + "_expires_at": now + timedelta(seconds=60)}
    )
    store.rotate_session(value, None)
    if kind == "absolute":
        refreshed = store.get_session(
            value.session_hash, value.configuration, now + timedelta(seconds=1), 1800
        )
        assert refreshed.idle_expires_at == refreshed.absolute_expires_at
    assert (
        store.get_session(
            value.session_hash, value.configuration, now + timedelta(seconds=60), 1800
        )
        is None
    )
    assert (
        store.get_session(
            value.session_hash, value.configuration, now + timedelta(seconds=61), 1800
        )
        is None
    )


def test_atomic_rotation_collision_rolls_back(owned_database):
    now = datetime.now(UTC).replace(microsecond=0)
    first, second = MongoBffStore(owned_database), MongoBffStore(owned_database)
    old, new, another = (make_session(now, key) for key in ("old", "new", "another"))
    first.rotate_session(old, None)
    second.rotate_session(new, old.session_hash)
    assert first.get_session("old", "configuration", now, 1800) is None
    assert first.get_session("new", "configuration", now, 1800)
    first.rotate_session(another, None)
    with pytest.raises(BffUnavailable):
        second.rotate_session(new, another.session_hash)
    assert first.get_session("another", "configuration", now, 1800)
    assert owned_database.bff_sessions.count_documents({}) == 2
    for document in owned_database.bff_sessions.find({}):
        assert not {
            "id_token",
            "access_token",
            "refresh_token",
            "session_token",
        }.intersection(document)


def test_provisioning_utc_unique_constraints(owned_database):
    now = datetime.now(UTC).replace(microsecond=0)
    db = owned_database
    db.b2b_users.insert_one(
        {
            "user_id": "user",
            "external_identity": {
                "issuer": IDENTITY.issuer,
                "subject": IDENTITY.subject,
            },
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    db.b2b_organizations.insert_one(
        {
            "organization_id": "org",
            "name": "Synthetic",
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    db.b2b_memberships.insert_one(
        {
            "membership_id": "m",
            "user_id": "user",
            "organization_id": "org",
            "role": "USER",
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    user = MongoIdentityRepository(db).find_by_external_identity(IDENTITY)[0]
    assert user.created_at.tzinfo and user.user_id == "user"
    assert MongoOrganizationRepository(db).get("org").status.value == "active"
    assert MongoMembershipRepository(db).find_for_user("user")[0].role.value == "USER"
    duplicate = db.b2b_users.find_one({"user_id": "user"})
    duplicate.pop("_id")
    duplicate["user_id"] = "other"
    with pytest.raises(DuplicateKeyError):
        db.b2b_users.insert_one(duplicate)
    duplicate = db.b2b_memberships.find_one({"membership_id": "m"})
    duplicate.pop("_id")
    duplicate["membership_id"] = "other"
    with pytest.raises(DuplicateKeyError):
        db.b2b_memberships.insert_one(duplicate)
    duplicate.pop("_id", None)
    duplicate["status"] = "invited"
    db.b2b_memberships.insert_one(duplicate)
    assert len(MongoMembershipRepository(db).find_for_user("user")) == 2
