"""Explicit v0.2.4 transition for StockPilot-B2B-v021 only.

No generic URI CLI, no calls to MigrationRunner.apply/private methods, no repair.
The host wrapper must attest backup, image, writers and the shared recovery lock.
This module performs no connection or mutation on import.
"""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from uuid import uuid4

INSTALLATION = "cce6d56f-3202-4008-bad2-37b01d70e130"
DATABASE = "stockpilot_b2b_test_cce6d56f32024008bad237b01d70e130"
REPLICA_ID = "6ae6f72cac20d999fb3d2d01"
MEMBERS = ((0, "mongo:27017"),)
IDENTITY_FILE = Path("/run/stockpilot/vm-id")
HISTORY = "stockpilot_schema_migrations"
LOCKS = "stockpilot_schema_migration_locks"
CHECKSUMS = (
    "516eefb24ab12ec8369d0ddc0b72cf5c00a620ab1f4471c090a8ff6077e0b2bc",
    "3ffb411de4f26bc5f876eafe6c263a29bba123a05758b34f105c0bf547ccd000",
    "47282658e0d2e3b4fae9a6f39fb9bb132e773e8c2a34b5dd3fd7d38ff8a858ff",
    "3823cefd97a2675c2fd5140478baf43c09f3985276e28bf1d38d5e1765313b8a",
    "cef28bc9a1b42157299475168ca9268f82afa7146703d07b41622db049a253ec",
    "b76e9c8a5ca86adcddea9ce4d106bc3d218e32bae015cbfd77b40e7a51146146",
)
ADDITIONS = frozenset({"b2b_order_archives", "b2b_order_archive_operations",
                      "b2b_contact_requests", "b2b_contact_replies", "b2b_contact_operations"})


class TransitionRefused(RuntimeError):
    """Bounded diagnostic without credentials or business records."""


def require(value, message):
    if not value:
        raise TransitionRefused(message)


def target(db):
    require(IDENTITY_FILE.is_file() and not IDENTITY_FILE.is_symlink()
            and IDENTITY_FILE.read_text().strip() == INSTALLATION,
            "Installation identity mismatch.")
    require(db.name == DATABASE, "This operator is bound to one installation database.")
    hello = db.client.admin.command("hello")
    require(hello.get("isWritablePrimary") is True and hello.get("setName") == "rs0",
            "Expected writable replica set required.")
    config = db.client.admin.command("replSetGetConfig")["config"]
    require(str(config.get("settings", {}).get("replicaSetId")) == REPLICA_ID
            and config.get("_id") == "rs0"
            and tuple((m["_id"], m["host"]) for m in config["members"]) == MEMBERS,
            "Mongo server identity mismatch.")


def tracking(db):
    for name in (HISTORY, LOCKS):
        collections = list(db.list_collections(filter={"name": name}))
        require(len(collections) == 1 and collections[0].get("type") == "collection",
                "Existing tracking collection required.")
        options = collections[0].get("options", {})
        require(set(options) <= {"collation"} and options.get("collation", {"locale": "simple"}).get("locale") == "simple"
                and {i["name"] for i in db[name].list_indexes()} == {"_id_"},
                "Tracking structure mismatch.")


def data_facts(db):
    from bson import json_util
    facts = {}
    for collection in db.list_collections():
        name = collection["name"]
        if name in {HISTORY, LOCKS}:
            continue
        canonical = lambda x: json_util.dumps(x, sort_keys=True, json_options=json_util.CANONICAL_JSON_OPTIONS)
        rows = sorted(canonical(r) for r in db[name].find({}))
        indexes = sorted(({**dict(i), "key": list(i["key"].items())} for i in db[name].list_indexes()),
                         key=lambda i: i["name"])
        facts[name] = {"data": hashlib.sha256("\n".join(rows).encode()).hexdigest(), "count": len(rows),
                       "structure": hashlib.sha256(canonical([collection.get("options", {}), indexes]).encode()).hexdigest()}
    return facts


def assert_business_equal(before, after):
    require(set(after) == set(before) | ADDITIONS, "Unexpected collection inventory after transition.")
    for name, value in before.items():
        observed = after[name]
        if name in {"bff_sessions", "bff_login_transactions"}:
            require(value["structure"] == observed["structure"], "Ephemeral collection structure changed.")
        else:
            require(value == observed, "Existing business data/structure changed.")


def transition(db):
    """Apply only 0005 then 0006 after fixed target admission. No automatic retry."""
    from pymongo import ReadPreference
    from pymongo.errors import DuplicateKeyError
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern
    from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
        MigrationRunner, collection_definitions, registry, verify_structure,
    )
    db = db.with_options(read_preference=ReadPreference.PRIMARY, read_concern=ReadConcern("majority"),
                         write_concern=WriteConcern("majority", wtimeout=10000))
    target(db)
    migrations = registry()
    require(tuple(m.checksum for m in migrations) == CHECKSUMS,
            "Approved immutable migration checksums differ.")
    runner = MigrationRunner(db, migrations=migrations)  # read-only validate/require_version only
    history = runner.validate()
    require(len(history) in {4, 6}, "Only complete v4 or an already complete v6 is accepted.")
    runner.require_version(len(history))
    tracking(db)
    require(db[LOCKS].count_documents({}) == 0, "A lock exists; never take over.")
    for name in ("b2b_stock_operations", "b2b_product_archives"):
        require(db[name].count_documents({"status": "pending"}) == 0, "Pending business operation.")
    before = data_facts(db)
    if len(history) == 6:
        require(ADDITIONS <= set(before), "Completed registry and collection inventory disagree.")
        return {"status": "already-complete", "version": 6, "writes": 0}
    require(not ADDITIONS.intersection(before), "Additive collection already present before 0005.")
    owner = uuid4().hex
    acquired = datetime.now(UTC)
    try:
        db[LOCKS].insert_one({"_id": "schema", "owner": owner, "acquired_at": acquired,
                             "expires_at": acquired + timedelta(seconds=300)})
    except DuplicateKeyError:
        raise TransitionRefused("Concurrent or expired lock; no takeover.") from None

    def lock():
        require(db[LOCKS].find_one({"_id": "schema", "owner": owner,
                                   "expires_at": {"$gt": datetime.now(UTC)}}) is not None,
                "Lock lost or expired; controlled recovery required.")

    version = None
    try:
        target(db)
        require(runner.validate() == history, "History changed while acquiring the lock.")
        runner.require_version(4)
        for migration in migrations[4:6]:
            version = migration.version
            lock()
            db[HISTORY].insert_one({"_id": version, "migration_id": migration.migration_id,
                                   "description": migration.description, "checksum": migration.checksum,
                                   "status": "started", "started_at": datetime.now(UTC),
                                   "finished_at": None, "runner_id": owner})
            for definition in collection_definitions(migration.definition):
                lock()
                require(not db.list_collection_names(filter={"name": definition["collection"]}),
                        "Collection appeared concurrently; no alteration attempted.")
                db.create_collection(definition["collection"], **definition["options"])
                for index in definition["indexes"]:
                    lock()
                    db[definition["collection"]].create_index(
                        [tuple(k) for k in index["key"]], name=index["name"], unique=index.get("unique", False))
                lock()
                verify_structure(db, definition)
            lock()
            result = db[HISTORY].update_one({"_id": version, "runner_id": owner, "status": "started"},
                                           {"$set": {"status": "succeeded", "finished_at": datetime.now(UTC)}})
            require(result.modified_count == 1, "Success write unconfirmed.")
            runner.require_version(version)
        lock()
        require(list(db[HISTORY].find({"_id": {"$lte": 4}}).sort("_id", 1)) == history,
                "Existing migration history changed.")
        assert_business_equal(before, data_facts(db))
        runner.require_version(6)
        lock()
        removed = db[LOCKS].delete_one({"_id": "schema", "owner": owner})
        require(removed.deleted_count == 1, "Lock release unconfirmed.")
        return {"status": "completed", "version": 6, "applied": [5, 6],
                "checksums": list(CHECKSUMS), "five_collections": sorted(ADDITIONS),
                "existing_business_data_equal": True, "runner_id": owner}
    except Exception:
        # Retain the lock on every failure, including an uncertain success write.
        # Record failure only while still the owner; never rewrite a succeeded row.
        if version is not None:
            try:
                lock()
                db[HISTORY].update_one({"_id": version, "runner_id": owner, "status": "started"},
                    {"$set": {"status": "failed", "finished_at": datetime.now(UTC),
                              "error": "v024_installation_transition_failed"}})
            except Exception:
                pass
        raise TransitionRefused("Transition incomplete or uncertain; lock retained, no automatic recovery.") from None
