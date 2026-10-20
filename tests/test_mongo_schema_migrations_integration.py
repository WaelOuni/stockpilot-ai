"""Versioned migrations on guarded disposable MongoDB, including real races."""

import hashlib
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from integration_targets import validate_mongo_test_uri, verify_mongo_identity
from pymongo import MongoClient
from test_tenant_catalogue_mongo_integration import db as db  # noqa: PLC0414

from stockpilot.infrastructure.persistence.mongodb.migrate import main as migration_cli
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    HISTORY,
    LOCKS,
    MigrationError,
    MigrationRunner,
    registry,
)

pytestmark = pytest.mark.integration


def apply(db, **kwargs):
    runner = MigrationRunner(db, **kwargs)
    runner.apply(confirmation=db.name)
    return runner


def test_empty_read_commands_write_nothing(db):
    runner = MigrationRunner(db)
    assert runner.status() == {"database": db.name, "history": [], "lock": None}
    assert [m.version for m in runner.plan()] == [1]
    assert runner.validate() == []
    assert not db.list_collection_names()
    with pytest.raises(MigrationError):
        runner.require_version()
    assert not db.list_collection_names()


def test_cli_real_read_commands_then_explicit_apply(db, monkeypatch, capsys):
    monkeypatch.setenv("STOCKPILOT_MIGRATION_URI", os.environ["MONGO_TEST_URI"])
    for command in ("status", "plan", "validate"):
        assert migration_cli([command, "--database", db.name]) == 0
        assert not db.list_collection_names()
    assert (
        migration_cli(
            ["apply", "--database", db.name, "--confirm-disposable-database", db.name]
        )
        == 0
    )
    MigrationRunner(db).require_version()
    assert not capsys.readouterr().err


def test_initial_migration_rerun_and_legacy_untouched(db):
    db.products.insert_one({"sku": "LEGACY", "price": 3, "stock": 5})
    original = list(db.products.find())
    indexes = list(db.products.list_indexes())
    runner = apply(db)
    history = runner.validate()
    assert len(history) == 1
    assert history[0]["checksum"] == registry()[0].checksum
    assert history[0]["status"] == "succeeded"
    assert history[0]["started_at"] <= history[0]["finished_at"]
    assert not runner.plan()
    runner.require_version()
    runner.apply(confirmation=db.name)
    assert runner.validate() == history
    assert db.b2b_products.count_documents({}) == 0
    assert list(db.products.find()) == original
    assert list(db.products.list_indexes()) == indexes
    assert db[LOCKS].count_documents({}) == 0


@pytest.mark.parametrize(
    "change", ["checksum", "id", "gap", "unknown", "started", "failed", "timestamps"]
)
def test_history_divergence_blocks_read_plan_and_apply(db, change):
    runner = apply(db)
    if change == "checksum":
        db[HISTORY].update_one({"_id": 1}, {"$set": {"checksum": "0" * 64}})
    elif change == "id":
        db[HISTORY].update_one({"_id": 1}, {"$set": {"migration_id": "other"}})
    elif change == "gap":
        row = db[HISTORY].find_one({"_id": 1})
        db[HISTORY].delete_one({"_id": 1})
        row["_id"] = 2
        db[HISTORY].insert_one(row)
    elif change == "unknown":
        db[HISTORY].insert_one({"_id": 999, "status": "succeeded"})
    elif change in ("started", "failed"):
        db[HISTORY].update_one({"_id": 1}, {"$set": {"status": change}})
    else:
        db[HISTORY].update_one({"_id": 1}, {"$unset": {"finished_at": ""}})
    assert runner.status()["history"]
    before = runner.status()
    for operation in (
        runner.validate,
        runner.plan,
        runner.require_version,
        lambda: runner.apply(confirmation=db.name),
    ):
        with pytest.raises(MigrationError):
            operation()
    assert runner.status() == before


def test_changed_migration_content_refused(db):
    apply(db)
    original = registry()[0]
    # A new checkout whose migration bytes differ has a different fingerprint.
    import hashlib

    content = original.content + b"\n"
    changed = replace(
        original, checksum=hashlib.sha256(content).hexdigest(), content=content
    )
    with pytest.raises(MigrationError):
        MigrationRunner(db, [changed]).validate()
    with pytest.raises(MigrationError):
        MigrationRunner(db, [])
    with pytest.raises(MigrationError):
        MigrationRunner(db, [replace(original, version=2)])


@pytest.mark.parametrize("expired", [False, True])
def test_present_and_expired_lock_never_stolen(db, expired):
    now = datetime.now(UTC)
    db[LOCKS].insert_one(
        {
            "_id": "schema",
            "owner": "other-runner",
            "expires_at": now + timedelta(seconds=-1 if expired else 300),
        }
    )
    before = list(db[LOCKS].find())
    with pytest.raises(MigrationError, match="lock present"):
        apply(db)
    assert list(db[LOCKS].find()) == before
    assert not list(db[HISTORY].find())


def test_two_real_runners_serialize(db):
    entered, finish = Event(), Event()

    class PausedRunner(MigrationRunner):
        def _execute(self, migration, owner):
            entered.set()
            assert finish.wait(10)
            return super()._execute(migration, owner)

    runner = PausedRunner(db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(runner.apply, confirmation=db.name)
        assert entered.wait(10)
        try:
            with pytest.raises(MigrationError):
                apply(db)
        finally:
            finish.set()
        future.result(timeout=10)
    assert len(MigrationRunner(db).validate()) == 1
    assert db[LOCKS].count_documents({}) == 0


def test_simultaneous_empty_history_runners_contend_for_same_lock(db):
    ready, loser_finished = Barrier(2), Event()

    class RacingRunner(MigrationRunner):
        def __init__(self, database):
            super().__init__(database)
            self.first_validation = True

        def validate(self):
            rows = super().validate()
            if self.first_validation:
                self.first_validation = False
                assert not rows
                ready.wait(timeout=10)
            return rows

        def _execute(self, migration, owner):
            assert loser_finished.wait(10)
            return super()._execute(migration, owner)

    def run(_):
        try:
            RacingRunner(db).apply(confirmation=db.name)
            return "applied"
        except MigrationError:
            loser_finished.set()
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(run, range(2))) == ["applied", "refused"]
    assert len(MigrationRunner(db).validate()) == 1
    assert not db[LOCKS].find_one({"_id": "schema"})


def test_simulated_crash_leaves_started_lock_and_partial_ddl(db):
    class Crash(BaseException):
        pass

    class CrashingRunner(MigrationRunner):
        def _execute(self, migration, owner):
            data = migration.definition
            self.db.create_collection(data["collection"], **data["options"])
            raise Crash()

    with pytest.raises(Crash):
        CrashingRunner(db).apply(confirmation=db.name)
    assert db[HISTORY].find_one({"_id": 1})["status"] == "started"
    assert db[LOCKS].find_one({"_id": "schema"})
    assert db.b2b_products.count_documents({}) == 0
    assert len(list(db.b2b_products.list_indexes())) == 1
    with pytest.raises(MigrationError):
        apply(db)
    with pytest.raises(MigrationError):
        MigrationRunner(db).require_version()


def test_handled_failure_stops_before_next_migration(db):
    first = registry()[0]
    data = {
        **first.definition,
        "version": 2,
        "id": "0002_fixture",
        "description": "Never execute",
    }
    content = json.dumps(data).encode()
    second = replace(
        first,
        version=2,
        migration_id="0002_fixture",
        description="Never execute",
        content=content,
        checksum=hashlib.sha256(content).hexdigest(),
    )

    class FailingRunner(MigrationRunner):
        def _execute(self, migration, owner):
            raise RuntimeError("private credentials must not persist")

    with pytest.raises(MigrationError) as error:
        FailingRunner(db, [first, second]).apply(confirmation=db.name)
    assert "credentials" not in str(error.value)
    rows = list(db[HISTORY].find())
    assert len(rows) == 1 and rows[0]["status"] == "failed"
    assert rows[0]["error"] == "migration_execution_failed"
    assert "credentials" not in str(rows)


def test_lock_deadline_expiry_does_not_record_success(db):
    now = [datetime.now(UTC)]

    class ExpiringRunner(MigrationRunner):
        def _execute(self, migration, owner):
            now[0] += timedelta(seconds=2)
            super()._execute(migration, owner)

    with pytest.raises(MigrationError):
        ExpiringRunner(db, clock=lambda: now[0], lease_seconds=1).apply(
            confirmation=db.name
        )
    assert db[HISTORY].find_one({"_id": 1})["status"] == "failed"


def test_safe_existing_partial_structure_can_be_completed_without_history(db):
    migration = registry()[0].definition
    db.create_collection(migration["collection"], **migration["options"])
    apply(db).require_version()


@pytest.mark.parametrize(
    "cause", ["unique", "partial", "global_sku", "collation", "validator", "view"]
)
def test_incompatible_structures_refused_never_dropped(db, cause):
    definition = registry()[0].definition
    if cause == "view":
        db.create_collection("b2b_products", viewOn="products")
    else:
        options = definition["options"].copy()
        if cause == "validator":
            options["validator"] = {}
        if cause == "collation":
            options["collation"] = {"locale": "fr"}
        db.create_collection("b2b_products", **options)
        if cause == "unique":
            db.b2b_products.create_index(
                [("organization_id", 1), ("sku", 1)],
                name="b2b_org_sku_unique",
                unique=False,
            )
        if cause == "partial":
            db.b2b_products.create_index(
                [("organization_id", 1), ("sku", 1)],
                name="b2b_org_sku_unique",
                unique=True,
                partialFilterExpression={"status": "active"},
            )
        if cause == "global_sku":
            db.b2b_products.create_index("sku", unique=True, name="bad_global_sku")
    options_before = db.b2b_products.options()
    with pytest.raises(MigrationError):
        apply(db)
    assert db[HISTORY].find_one({"_id": 1})["status"] == "failed"
    assert db.b2b_products.options() == options_before


@pytest.mark.parametrize("confirmation", [None, "", "stockpilot", "wrong-target"])
def test_missing_explicit_identification_refused_before_writes(db, confirmation):
    with pytest.raises(MigrationError):
        MigrationRunner(db).apply(confirmation=confirmation)
    assert not db.list_collection_names()


def test_protected_database_name_refused_before_writes(db):
    # Never connect or write to a real protected DB: only construct a handle.
    with pytest.raises(MigrationError):
        MigrationRunner(db.client["stockpilot"]).apply(confirmation="stockpilot")


def test_database_outage_is_explicit():
    with MongoClient(
        "mongodb://127.0.0.1:1", serverSelectionTimeoutMS=100, connectTimeoutMS=100
    ) as client:
        runner = MigrationRunner(client["stockpilot_b2b_test_" + "a" * 32])
        for action in (
            runner.status,
            runner.plan,
            runner.validate,
            lambda: runner.apply(confirmation=runner.db.name),
        ):
            with pytest.raises(MigrationError):
                action()


@pytest.mark.parametrize("name", [HISTORY, LOCKS])
def test_tracking_ttl_or_capped_collection_refused(db, name):
    db[name].create_index("expires_at", expireAfterSeconds=0)
    with pytest.raises(MigrationError, match="tracking"):
        apply(db)
    assert db.b2b_products.count_documents({}) == 0


def test_real_read_only_credentials_cannot_apply():
    from stockpilot.infrastructure.config.settings import Settings

    # A separate auth-enabled tmpfs server, never the application's Mongo.
    uri = os.environ.get("MONGO_MIGRATION_AUTH_TEST_URI")
    assert uri, "Explicit MONGO_MIGRATION_AUTH_TEST_URI required for permission test"
    validate_mongo_test_uri({"MONGO_TEST_URI": uri}, Settings())
    name = "stockpilot_b2b_test_" + uuid4().hex
    password = secrets.token_urlsafe(32)
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as root:
        verify_mongo_identity(root)
        database = root[name]
        database.fixture_marker.insert_one({"disposable": True})
        database.command(
            "createUser", "readonly", pwd=password, roles=[{"role": "read", "db": name}]
        )
        try:
            # Reuse explicit parsed endpoint; do not interpolate credentials into URI.
            from pymongo.uri_parser import parse_uri

            host, port = parse_uri(uri)["nodelist"][0]
            with MongoClient(
                host,
                port,
                username="readonly",
                password=password,
                authSource=name,
                serverSelectionTimeoutMS=5000,
            ) as limited:
                runner = MigrationRunner(limited[name])
                assert runner.plan()
                with pytest.raises(MigrationError):
                    runner.apply(confirmation=name)
                assert set(database.list_collection_names()) == {"fixture_marker"}
        finally:
            root.drop_database(name)


def test_next_schema_snapshot_adds_index_and_previous_gate_stays_usable(db):
    first = registry()[0]
    data = {
        **first.definition,
        "version": 2,
        "id": "0002_fixture",
        "description": "Synthetic cumulative index addition",
    }
    data["indexes"] = [
        *data["indexes"],
        {"name": "b2b_org_revision", "key": [["organization_id", 1], ["revision", 1]]},
    ]
    content = json.dumps(data).encode()
    second = replace(
        first,
        version=2,
        migration_id=data["id"],
        description=data["description"],
        content=content,
        checksum=hashlib.sha256(content).hexdigest(),
    )
    apply(db)
    runner = MigrationRunner(db, [first, second])
    runner.apply(confirmation=db.name)
    assert [row["_id"] for row in runner.validate()] == [1, 2]
    assert len(list(db.b2b_products.list_indexes())) == 6
    runner.require_version(1)
    runner.require_version(2)
    runner.apply(confirmation=db.name)
    assert not runner.plan()
