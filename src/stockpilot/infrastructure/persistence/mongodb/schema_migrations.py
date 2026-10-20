"""Explicit declarative migrations. Importing this module performs no I/O.

DDL is not a multi-statement transaction. A crash/failed migration blocks apply;
no lock stealing, forced repair, destructive rollback or automatic resume.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pymongo import ReadPreference
from pymongo.errors import CollectionInvalid, DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

HISTORY = "stockpilot_schema_migrations"
LOCKS = "stockpilot_schema_migration_locks"
LOCK_ID = "schema"
TARGET = re.compile(r"stockpilot_b2b_test_[0-9a-f]{32}")
log = logging.getLogger(__name__)
B2B_COLLECTIONS = frozenset(
    {
        "b2b_products",
        "b2b_buyers",
        "b2b_orders",
        "b2b_order_operations",
        "b2b_stock_movements",
        "b2b_imports",
        "b2b_stock_operations",
    }
)


def collection_definitions(data):
    """0001 stays byte-for-byte immutable; 0002 can group related collections."""
    definitions = data.get("collections", [data])
    names = [d["collection"] for d in definitions]
    if (
        not names
        or len(set(names)) != len(names)
        or any(name not in B2B_COLLECTIONS for name in names)
    ):
        raise MigrationError("Migration collection is outside the B2B allowlist.")
    return definitions


class MigrationError(Exception):
    """Sanitized migration failure. Driver errors must not be printed."""


@dataclass(frozen=True)
class Migration:
    version: int
    migration_id: str
    description: str
    checksum: str
    content: bytes

    @classmethod
    def load(cls, path: Path):
        content = path.read_bytes()
        data = json.loads(content)
        version = data["version"]
        if (
            type(version) is not int
            or version < 1
            or data["id"] != path.stem
            or not path.stem.startswith(f"{version:04d}_")
            or not data["description"]
        ):
            raise MigrationError("Invalid migration definition.")
        collection_definitions(data)
        return cls(
            version,
            data["id"],
            data["description"],
            hashlib.sha256(content).hexdigest(),
            content,
        )

    @property
    def definition(self):
        return json.loads(self.content)


def registry() -> tuple[Migration, ...]:
    try:
        return tuple(
            Migration.load(p)
            for p in sorted(Path(__file__).with_name("migrations").glob("*.json"))
        )
    except (OSError, ValueError, KeyError, TypeError):
        raise MigrationError("Migration registry unavailable or malformed.") from None


def effective_migrations(migrations):
    """Latest complete schema snapshot per collection; history stays immutable."""
    return tuple(
        {
            d["collection"]: d
            for m in migrations
            for d in collection_definitions(m.definition)
        }.values()
    )


def _database(db):
    return db.with_options(
        read_preference=ReadPreference.PRIMARY,
        read_concern=ReadConcern("majority"),
        write_concern=WriteConcern("majority", wtimeout=10000),
    )


def verify_structure(db, migration: Migration) -> None:
    data = migration if isinstance(migration, dict) else migration.definition
    name = data["collection"]
    records = list(db.list_collections(filter={"name": name}))
    if len(records) != 1 or records[0].get("type") != "collection":
        raise MigrationError("Required B2B collection unavailable.")
    options = records[0].get("options", {})
    # Mongo expands simple collation to an omitted/default collation.
    actual_collation = options.get("collation", {"locale": "simple"})
    if (
        actual_collation.get("locale") != "simple"
        or any(
            options.get(k) != v for k, v in data["options"].items() if k != "collation"
        )
        or options.get("capped")
        or options.get("timeseries")
    ):
        raise MigrationError("Incompatible B2B collection options.")
    expected = {i["name"]: i for i in data["indexes"]}
    indexes = {i["name"]: i for i in db[name].list_indexes()}
    # Extra indexes can impose global SKU uniqueness, TTL or hidden semantics.
    if set(indexes) != set(expected) | {"_id_"}:
        raise MigrationError("Missing or unexpected B2B index.")
    for name, spec in expected.items():
        actual = indexes[name]
        if (
            list(actual["key"].items()) != [tuple(k) for k in spec["key"]]
            or bool(actual.get("unique")) != bool(spec.get("unique"))
            or any(
                k in actual
                for k in (
                    "partialFilterExpression",
                    "expireAfterSeconds",
                    "sparse",
                    "hidden",
                )
            )
            or actual.get("collation", {"locale": "simple"}).get("locale") != "simple"
        ):
            raise MigrationError("Incompatible B2B index options.")


class MigrationRunner:
    def __init__(self, database, migrations=None, *, clock=None, lease_seconds=300):
        self.db = _database(database)
        self.migrations = registry() if migrations is None else tuple(migrations)
        self.clock = clock or (lambda: datetime.now(UTC))
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise MigrationError("Invalid lock deadline.")
        self.lease_seconds = lease_seconds
        if (
            not self.migrations
            or [m.version for m in self.migrations]
            != list(range(1, len(self.migrations) + 1))
            or len({m.migration_id for m in self.migrations}) != len(self.migrations)
        ):
            raise MigrationError(
                "Migration registry must be unique, ordered and contiguous from 1."
            )
        for m in self.migrations:
            data = m.definition
            if (
                hashlib.sha256(m.content).hexdigest() != m.checksum
                or data["version"] != m.version
                or data["id"] != m.migration_id
                or data["description"] != m.description
            ):
                raise MigrationError("Migration content fingerprint mismatch.")
            collection_definitions(data)

    def _history(self):
        return list(self.db[HISTORY].find({}).sort("_id", 1))

    def validate(self):
        """Read-only, including on a completely empty database."""
        try:
            rows = self._history()
            if len(rows) > len(self.migrations):
                raise MigrationError("Unknown recorded migration.")
            for m, row in zip(self.migrations, rows, strict=False):
                if (
                    type(row.get("_id")) is not int
                    or row["_id"] != m.version
                    or row.get("migration_id") != m.migration_id
                    or row.get("checksum") != m.checksum
                    or row.get("description") != m.description
                ):
                    raise MigrationError(
                        "Migration history gap, unknown version or fingerprint divergence."
                    )
                if (
                    row.get("status") != "succeeded"
                    or not isinstance(row.get("started_at"), datetime)
                    or not isinstance(row.get("finished_at"), datetime)
                    or row["finished_at"] < row["started_at"]
                    or not row.get("runner_id")
                ):
                    raise MigrationError(
                        "Migration incomplete or failed; human recovery required."
                    )
            return rows
        except PyMongoError:
            raise MigrationError("Migration history unavailable.") from None

    def status(self):
        try:
            # Show incomplete states even when validate refuses them.
            return {
                "database": self.db.name,
                "history": self._history(),
                "lock": self.db[LOCKS].find_one({"_id": LOCK_ID}),
            }
        except PyMongoError:
            raise MigrationError("Migration status unavailable.") from None

    def plan(self):
        rows = self.validate()
        return self.migrations[len(rows) :]

    def require_version(self, version=1):
        rows = self.validate()
        if type(version) is not int or version < 1 or len(rows) < version:
            raise MigrationError("Required B2B schema version not applied.")
        try:
            for m in effective_migrations(self.migrations[: len(rows)]):
                verify_structure(self.db, m)
        except PyMongoError:
            raise MigrationError("B2B schema verification unavailable.") from None

    def _target(self, confirmation):
        if not TARGET.fullmatch(self.db.name) or confirmation != self.db.name:
            raise MigrationError(
                "Explicit disposable database identification required."
            )
        # Refuse a dedicated-looking name on a server hosting protected data.
        names = self.db.client.list_database_names()
        if any(
            n in {"stockpilot", "stockpilot_e2e"}
            or n.startswith(("stockpilot_demo", "stockpilot_candidate"))
            for n in names
        ):
            raise MigrationError("Server hosts protected application databases.")
        for name in set(names) - {"admin", "local", "config"}:
            if self.db.client[name].list_collection_names(
                filter={"name": "demo_instance"}
            ):
                raise MigrationError("Server hosts a DEMO marker.")
        if not self.db.client.admin.command("hello").get("isWritablePrimary"):
            raise MigrationError("Writable Mongo primary required.")

    def _assert_lock(self, owner):
        lock = self.db[LOCKS].find_one(
            {"_id": LOCK_ID, "owner": owner, "expires_at": {"$gt": self.clock()}}
        )
        if lock is None:
            raise MigrationError(
                "Migration lock lost or deadline expired; manual recovery required."
            )

    def _verify_tracking(self):
        for name in (HISTORY, LOCKS):
            records = list(self.db.list_collections(filter={"name": name}))
            if not records:
                continue
            options = records[0].get("options", {})
            if (
                records[0].get("type") != "collection"
                or any(k != "collation" for k in options)
                or options.get("collation", {"locale": "simple"}).get("locale")
                != "simple"
                or {i["name"] for i in self.db[name].list_indexes()} != {"_id_"}
            ):
                raise MigrationError(
                    "Incompatible migration tracking collection; refusing unsafe locking/history."
                )

    def _execute(self, migration, owner):
        for data in collection_definitions(migration.definition):
            self._execute_collection(data, owner)

    def _execute_collection(self, data, owner):
        self._assert_lock(owner)
        if not self.db.list_collection_names(filter={"name": data["collection"]}):
            try:
                self.db.create_collection(data["collection"], **data["options"])
            except CollectionInvalid:
                raise MigrationError(
                    "Concurrent collection creation refused."
                ) from None
        else:
            # Verify options BEFORE creating any indexes on an existing collection.
            options = self.db[data["collection"]].options()
            if (
                any(
                    options.get(k) != v
                    for k, v in data["options"].items()
                    if k != "collation"
                )
                or options.get("collation", {"locale": "simple"}).get("locale")
                != "simple"
                or options.get("capped")
                or options.get("timeseries")
            ):
                raise MigrationError(
                    "Existing B2B collection is incompatible; no alteration attempted."
                )
        for spec in data["indexes"]:
            self._assert_lock(owner)
            self.db[data["collection"]].create_index(
                [tuple(k) for k in spec["key"]],
                name=spec["name"],
                unique=spec.get("unique", False),
            )
        verify_structure(self.db, data)

    def apply(self, *, confirmation=None):
        """Only explicitly identified disposable targets in B1. Never at startup."""
        owner = uuid4().hex
        try:
            self._target(confirmation)
            self._verify_tracking()
            self.validate()
            now = self.clock()
            try:
                self.db[LOCKS].insert_one(
                    {
                        "_id": LOCK_ID,
                        "owner": owner,
                        "acquired_at": now,
                        "expires_at": now + timedelta(seconds=self.lease_seconds),
                    }
                )
            except DuplicateKeyError:
                raise MigrationError(
                    "Migration lock present (including expired); no takeover permitted."
                ) from None
        except PyMongoError:
            raise MigrationError("Migration target or lock unavailable.") from None
        # BaseException simulates process death: leave lock + started record.
        try:
            pending = self.plan()
            for m in effective_migrations(
                self.migrations[: len(self.migrations) - len(pending)]
            ):
                verify_structure(self.db, m)
            for m in pending:
                self._assert_lock(owner)
                self.db[HISTORY].insert_one(
                    {
                        "_id": m.version,
                        "migration_id": m.migration_id,
                        "description": m.description,
                        "checksum": m.checksum,
                        "status": "started",
                        "started_at": self.clock(),
                        "finished_at": None,
                        "runner_id": owner,
                    }
                )
                log.info("Mongo migration %s started", m.migration_id)
                try:
                    self._execute(m, owner)
                    self._assert_lock(owner)
                except Exception:  # noqa: BLE001 - persist any failed DDL before stopping
                    self.db[HISTORY].update_one(
                        {"_id": m.version, "runner_id": owner, "status": "started"},
                        {
                            "$set": {
                                "status": "failed",
                                "finished_at": self.clock(),
                                "error": "migration_execution_failed",
                            }
                        },
                    )
                    raise MigrationError(
                        "Migration failed; inspect status and follow human recovery procedure."
                    ) from None
                result = self.db[HISTORY].update_one(
                    {"_id": m.version, "runner_id": owner, "status": "started"},
                    {"$set": {"status": "succeeded", "finished_at": self.clock()}},
                )
                if result.modified_count != 1:
                    raise MigrationError("Migration success could not be recorded.")
                log.info("Mongo migration %s succeeded", m.migration_id)
        except Exception:  # noqa: BLE001 - sanitize errors and release only our own lock
            try:
                self.db[LOCKS].delete_one({"_id": LOCK_ID, "owner": owner})
            except PyMongoError:
                pass  # Uncertain release remains blocking; never steal it.
            raise MigrationError(
                "Migration apply stopped; inspect persistent status."
            ) from None
        try:
            self.db[LOCKS].delete_one({"_id": LOCK_ID, "owner": owner})
        except PyMongoError:
            raise MigrationError(
                "Migration lock release unconfirmed; inspect persistent status."
            ) from None
