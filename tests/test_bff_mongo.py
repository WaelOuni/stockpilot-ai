"""Mongo adapter contracts tested without database or network access."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from pymongo import ReturnDocument
from pymongo.errors import AutoReconnect, DuplicateKeyError

from stockpilot.application.ports.bff_security import (
    BffSession,
    BffUnavailable,
    LoginTransaction,
)
from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
    MongoIdentityRepository,
    MongoMembershipRepository,
    MongoOrganizationRepository,
    initialize_b2b_indexes,
)
from stockpilot.infrastructure.persistence.mongodb.bff_store import MongoBffStore

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
IDENTITY = ExternalIdentity("https://issuer.example/realms/test", "subject")


def database():
    db = MagicMock()
    db.with_options.return_value = db
    collections = {}
    db.get_collection.side_effect = lambda name, **kwargs: collections.setdefault(
        name, MagicMock()
    )
    return db, collections


def transaction():
    return LoginTransaction(
        "state-hash",
        "browser-hash",
        "nonce",
        "verifier",
        "org",
        NOW + timedelta(minutes=5),
        "configuration",
        "old-session-hash",
    )


def session():
    return BffSession(
        "new-session-hash",
        IDENTITY,
        "user",
        "org",
        "csrf",
        NOW,
        NOW + timedelta(hours=8),
        NOW + timedelta(minutes=30),
        "configuration",
    )


def session_document():
    value = session()
    return {
        "_id": value.session_hash,
        "external_identity": {"issuer": IDENTITY.issuer, "subject": IDENTITY.subject},
        "user_id": value.user_id,
        "organization_id": value.organization_id,
        "csrf_token": value.csrf_token,
        "created_at": value.created_at,
        "absolute_expires_at": value.absolute_expires_at,
        "idle_expires_at": value.idle_expires_at,
        "configuration": value.configuration,
    }


def test_initialize_explicit_and_majority_utc():
    db, collections = database()
    store = MongoBffStore(db)
    for collection in collections.values():
        collection.create_index.assert_not_called()
        collection.insert_one.assert_not_called()
    store.initialize()
    assert collections["bff_login_transactions"].create_index.call_count == 1
    assert collections["bff_sessions"].create_index.call_count == 2
    for call in db.get_collection.call_args_list:
        assert call.kwargs["write_concern"].document == {
            "w": "majority",
            "wtimeout": 5000,
        }
    assert db.with_options.call_args.kwargs["codec_options"].tz_aware


def test_consume_atomic_bound_to_browser_config_expiry():
    db, collections = database()
    store = MongoBffStore(db)
    store.put_transaction(transaction())
    col = collections["bff_login_transactions"]
    document = col.insert_one.call_args.args[0]
    assert not {"id_token", "access_token", "refresh_token"}.intersection(document)
    col.find_one_and_delete.return_value = document
    assert (
        store.consume_transaction("state-hash", "browser-hash", "configuration", NOW)
        == transaction()
    )
    col.find_one_and_delete.assert_called_once_with(
        {
            "_id": "state-hash",
            "browser_hash": "browser-hash",
            "configuration": "configuration",
            "expires_at": {"$gt": NOW},
        }
    )
    col.find_one_and_delete.return_value = None
    assert store.consume_transaction("state", "browser", "config", NOW) is None


def test_get_checks_expiry_and_never_upserts():
    db, collections = database()
    store = MongoBffStore(db)
    col = collections["bff_sessions"]
    col.find_one_and_update.return_value = session_document()
    assert (
        store.get_session("new-session-hash", "configuration", NOW, 1800) == session()
    )
    call = col.find_one_and_update.call_args
    assert call.args[0] == {
        "_id": "new-session-hash",
        "configuration": "configuration",
        "absolute_expires_at": {"$gt": NOW},
        "idle_expires_at": {"$gt": NOW},
    }
    assert call.args[1] == [
        {
            "$set": {
                "idle_expires_at": {
                    "$min": ["$absolute_expires_at", NOW + timedelta(seconds=1800)]
                }
            }
        }
    ]
    assert call.kwargs == {"return_document": ReturnDocument.AFTER, "upsert": False}
    col.find_one_and_update.return_value = None
    assert store.get_session("missing", "configuration", NOW, 1800) is None


@pytest.mark.parametrize("previous", [None, "old-session-hash"])
def test_rotation_atomic_insert_and_config_bound_delete(previous):
    db, collections = database()
    store = MongoBffStore(db)
    mongo_session = db.client.start_session.return_value.__enter__.return_value
    mongo_session.with_transaction.side_effect = lambda callback, **kwargs: callback(
        mongo_session
    )
    store.rotate_session(session(), previous)
    col = collections["bff_sessions"]
    col.insert_one.assert_called_once_with(session_document(), session=mongo_session)
    if previous:
        col.delete_one.assert_called_once_with(
            {"_id": previous, "configuration": "configuration"}, session=mongo_session
        )
    else:
        col.delete_one.assert_not_called()
    assert mongo_session.with_transaction.call_args.kwargs["max_commit_time_ms"] == 5000


@pytest.mark.parametrize(
    "operation", ["initialize", "put", "consume", "get", "rotate", "revoke"]
)
def test_technical_errors_redacted(operation):
    db, collections = database()
    store = MongoBffStore(db)
    error = AutoReconnect("private database credentials")
    actions = {
        "initialize": (
            collections["bff_login_transactions"].create_index,
            store.initialize,
        ),
        "put": (
            collections["bff_login_transactions"].insert_one,
            lambda: store.put_transaction(transaction()),
        ),
        "consume": (
            collections["bff_login_transactions"].find_one_and_delete,
            lambda: store.consume_transaction("s", "b", "c", NOW),
        ),
        "get": (
            collections["bff_sessions"].find_one_and_update,
            lambda: store.get_session("s", "c", NOW, 60),
        ),
        "rotate": (
            db.client.start_session,
            lambda: store.rotate_session(session(), None),
        ),
        "revoke": (
            collections["bff_sessions"].delete_one,
            lambda: store.revoke_session("s", "c"),
        ),
    }
    target, action = actions[operation]
    target.side_effect = error
    with pytest.raises(BffUnavailable, match="^BFF state unavailable$"):
        action()


def test_programming_error_not_swallowed():
    db, collections = database()
    store = MongoBffStore(db)
    collections["bff_sessions"].delete_one.side_effect = RuntimeError("bug")
    with pytest.raises(RuntimeError):
        store.revoke_session("s", "c")


def user_document():
    return {
        "user_id": "user",
        "external_identity": {"issuer": IDENTITY.issuer, "subject": IDENTITY.subject},
        "status": "active",
        "created_at": NOW,
        "updated_at": NOW,
    }


def membership_document():
    return {
        "membership_id": "m",
        "user_id": "user",
        "organization_id": "org",
        "role": "USER",
        "status": "active",
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_identity_lookup_no_provisioning():
    db, collections = database()
    repo = MongoIdentityRepository(db)
    col = collections["b2b_users"]
    col.find.return_value.limit.return_value = [user_document()]
    assert repo.find_by_external_identity(IDENTITY)[0].user_id == "user"
    col.find.assert_called_once_with(
        {
            "external_identity.issuer": IDENTITY.issuer,
            "external_identity.subject": IDENTITY.subject,
        }
    )
    col.find.return_value.limit.return_value = []
    assert repo.find_by_external_identity(IDENTITY) == ()
    col.insert_one.assert_not_called()
    col.update_one.assert_not_called()
    col.create_index.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "unknown"),
        ("created_at", NOW.replace(tzinfo=None)),
        ("user_id", ""),
        ("external_identity", {}),
    ],
)
def test_malformed_identity_is_technical(field, value):
    db, collections = database()
    repo = MongoIdentityRepository(db)
    collections["b2b_users"].find.return_value.limit.return_value = [
        user_document() | {field: value}
    ]
    with pytest.raises(IdentityRepositoryError):
        repo.find_by_external_identity(IDENTITY)


def test_duplicate_identity_refused():
    db, collections = database()
    repo = MongoIdentityRepository(db)
    collections["b2b_users"].find.return_value.limit.return_value = [
        user_document(),
        user_document(),
    ]
    with pytest.raises(IdentityRepositoryError):
        repo.find_by_external_identity(IDENTITY)


def test_organization_missing_and_ambiguous():
    db, collections = database()
    repo = MongoOrganizationRepository(db)
    col = collections["b2b_organizations"]
    document = {
        "organization_id": "org",
        "name": "Test",
        "status": "active",
        "created_at": NOW,
        "updated_at": NOW,
    }
    col.find.return_value.limit.return_value = [document]
    assert repo.get("org").organization_id == "org"
    col.find.return_value.limit.return_value = []
    assert repo.get("unknown") is None
    col.find.return_value.limit.return_value = [document, document]
    with pytest.raises(IdentityRepositoryError):
        repo.get("org")


def test_membership_role_and_duplicates():
    db, collections = database()
    repo = MongoMembershipRepository(db)
    col = collections["b2b_memberships"]
    col.find.return_value = [membership_document()]
    assert repo.find_for_user("user")[0].role.value == "USER"
    for documents in (
        [membership_document() | {"role": "ADMIN"}],
        [membership_document(), membership_document() | {"membership_id": "other"}],
    ):
        col.find.return_value = documents
        with pytest.raises(IdentityRepositoryError):
            repo.find_for_user("user")


def test_unique_indexes_explicit():
    db, collections = database()
    initialize_b2b_indexes(db)
    assert any(
        call.kwargs.get("partialFilterExpression") == {"status": "active"}
        and call.kwargs["unique"]
        for call in collections["b2b_memberships"].create_index.call_args_list
    )
    assert collections["b2b_users"].create_index.call_count == 2
    collections["b2b_users"].create_index.side_effect = DuplicateKeyError(
        "private data"
    )
    with pytest.raises(IdentityRepositoryError):
        initialize_b2b_indexes(db)
