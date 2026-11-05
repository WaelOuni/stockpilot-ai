"""Real installation-transition algorithm on guarded disposable MongoDB only."""
from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from bson import Int64
from pymongo.database import Database
from test_tenant_catalogue_mongo_integration import db as db  # guarded MONGO_TEST_URI fixture

from scripts.ops import v024_installation_migration as op
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationError, MigrationRunner, registry,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def admitted(db, tmp_path, monkeypatch):
    # Test-only relocation after the existing fixture has attested a disposable
    # server and created its UUID database. The production CLI has no overrides.
    MigrationRunner(db, migrations=registry()[:4]).apply(confirmation=db.name)
    marker = tmp_path / "synthetic-installation"
    marker.write_text(op.INSTALLATION)
    cfg = db.client.admin.command("replSetGetConfig")["config"]
    monkeypatch.setattr(op, "DATABASE", db.name)
    monkeypatch.setattr(op, "IDENTITY_FILE", marker)
    monkeypatch.setattr(op, "REPLICA_ID", str(cfg["settings"]["replicaSetId"]))
    monkeypatch.setattr(op, "MEMBERS", tuple((m["_id"], m["host"]) for m in cfg["members"]))
    db.transition_probe.insert_one({"_id": "kept", "int32": 1, "int64": Int64(1), "double": 1.0})
    return db


def test_success_preserves_data_history_then_replay_performs_no_write(admitted):
    db = admitted
    old = list(db[op.HISTORY].find().sort("_id", 1))
    before = op.data_facts(db)
    result = op.transition(db)
    assert result["status"] == "completed" and result["applied"] == [5, 6]
    assert list(db[op.HISTORY].find({"_id": {"$lte": 4}}).sort("_id", 1)) == old
    op.assert_business_equal(before, op.data_facts(db))
    captured = list(db[op.HISTORY].find().sort("_id", 1))
    facts = op.data_facts(db)
    assert op.transition(db) == {"status": "already-complete", "version": 6, "writes": 0}
    assert list(db[op.HISTORY].find().sort("_id", 1)) == captured
    assert op.data_facts(db) == facts and db[op.LOCKS].count_documents({}) == 0


@pytest.mark.parametrize("failure_name,expected_version", [
    ("b2b_order_archives", 5), ("b2b_order_archive_operations", 5),
    ("b2b_contact_requests", 6), ("b2b_contact_replies", 6),
])
def test_partial_ddl_keeps_failed_history_lock_and_never_advances(admitted, monkeypatch, failure_name, expected_version):
    db = admitted
    original = Database.create_collection
    before = op.data_facts(db)
    def fail(database, name, *a, **kw):
        if database.name == db.name and name == failure_name:
            raise RuntimeError("synthetic DDL interruption")
        return original(database, name, *a, **kw)
    monkeypatch.setattr(Database, "create_collection", fail)
    with pytest.raises(op.TransitionRefused, match="retained"):
        op.transition(db)
    history = list(db[op.HISTORY].find().sort("_id", 1))
    assert history[-1]["_id"] == expected_version and history[-1]["status"] == "failed"
    assert db[op.LOCKS].count_documents({}) == 1
    for name, expected in before.items():
        assert op.data_facts(db)[name] == expected
    state = list(db[op.HISTORY].find())
    with pytest.raises((MigrationError, op.TransitionRefused)):
        op.transition(db)
    assert list(db[op.HISTORY].find()) == state


def test_lock_loss_prevents_success_and_preserves_unknown_owner(admitted, monkeypatch):
    db = admitted
    original = Database.create_collection
    def lose(database, name, *a, **kw):
        result = original(database, name, *a, **kw)
        if database.name == db.name and name == "b2b_order_archives":
            db[op.LOCKS].update_one({"_id": "schema"}, {"$set": {"owner": "synthetic-foreign-owner"}})
        return result
    monkeypatch.setattr(Database, "create_collection", lose)
    with pytest.raises(op.TransitionRefused):
        op.transition(db)
    assert db[op.LOCKS].find_one() ["owner"] == "synthetic-foreign-owner"
    assert db[op.HISTORY].find_one({"_id": 5})["status"] == "started"
    assert db[op.HISTORY].find_one({"_id": 6}) is None


@pytest.mark.parametrize("change", ["wrong-database", "wrong-installation", "wrong-server", "checksum", "incomplete", "expired-lock", "unknown-lock"])
def test_preconditions_fail_without_migration_effects(admitted, monkeypatch, change):
    db = admitted
    if change == "wrong-database":
        monkeypatch.setattr(op, "DATABASE", "different")
    elif change == "wrong-installation":
        op.IDENTITY_FILE.write_text(str(uuid4()))
    elif change == "wrong-server":
        monkeypatch.setattr(op, "REPLICA_ID", "0" * 24)
    elif change == "checksum":
        db[op.HISTORY].update_one({"_id": 4}, {"$set": {"checksum": "0" * 64}})
    elif change == "incomplete":
        db[op.HISTORY].delete_one({"_id": 2})
    else:
        db[op.LOCKS].insert_one({"_id": "schema", "owner": "foreign",
            "expires_at": datetime.now(UTC) + timedelta(seconds=-60 if change == "expired-lock" else 60)})
    before = {name: list(db[name].find()) for name in db.list_collection_names()}
    with pytest.raises((MigrationError, op.TransitionRefused)):
        op.transition(db)
    assert before == {name: list(db[name].find()) for name in db.list_collection_names()}


def test_concurrent_executor_cannot_steal_the_first_lock(admitted, monkeypatch):
    db = admitted
    entered, release = Event(), Event()
    original = Database.create_collection
    def pause(database, name, *a, **kw):
        if database.name == db.name and name == "b2b_order_archives":
            entered.set()
            assert release.wait(15)
        return original(database, name, *a, **kw)
    monkeypatch.setattr(Database, "create_collection", pause)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(op.transition, db)
        try:
            assert entered.wait(10)
            owner = db[op.LOCKS].find_one()["owner"]
            with pytest.raises((MigrationError, op.TransitionRefused)):
                op.transition(db)
            assert db[op.LOCKS].find_one()["owner"] == owner
        finally:
            release.set()
        assert future.result(timeout=20)["status"] == "completed"
    assert db[op.LOCKS].count_documents({}) == 0


def test_approved_checksums_cannot_be_replaced(admitted, monkeypatch):
    monkeypatch.setattr(op, "CHECKSUMS", (*op.CHECKSUMS[:5], "0" * 64))
    with pytest.raises(op.TransitionRefused, match="checksums"):
        op.transition(admitted)
    assert admitted[op.HISTORY].count_documents({}) == 4
    assert admitted[op.LOCKS].count_documents({}) == 0
