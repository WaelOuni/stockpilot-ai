"""Bounded recovery proofs and offline validation, never a migration operator.

Collection access is read-only. Callers must independently attest their target
and quiesce writers. No import-time I/O, connection, subprocess or automatic DDL.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile

FORMAT = "stockpilot-paired-recovery-v2"
HISTORY = "stockpilot_schema_migrations"
NEW_COLLECTIONS = frozenset({
    "b2b_order_archives", "b2b_order_archive_operations", "b2b_contact_requests",
    "b2b_contact_replies", "b2b_contact_operations",
})
EPHEMERAL = frozenset({"bff_sessions", "bff_login_transactions"})
BOOTSTRAP_ROLE_ATTRIBUTES = [True, True, True, True, True, True, -1, True, None]
CHECKSUMS = (
    "516eefb24ab12ec8369d0ddc0b72cf5c00a620ab1f4471c090a8ff6077e0b2bc",
    "3ffb411de4f26bc5f876eafe6c263a29bba123a05758b34f105c0bf547ccd000",
    "47282658e0d2e3b4fae9a6f39fb9bb132e773e8c2a34b5dd3fd7d38ff8a858ff",
    "3823cefd97a2675c2fd5140478baf43c09f3985276e28bf1d38d5e1765313b8a",
    "cef28bc9a1b42157299475168ca9268f82afa7146703d07b41622db049a253ec",
    "b76e9c8a5ca86adcddea9ce4d106bc3d218e32bae015cbfd77b40e7a51146146",
)
ARCHIVES = frozenset({"mongo.archive.gz", "application.dump", "keycloak.dump",
                      "application-globals.sql", "keycloak-globals.sql",
                      "configuration.tar.gz", "facts.json"})
PROVENANCE_PATHS = (
    "operations/backup_fresh.py", "operations/restore_isolated.py",
    "operations/recovery_facts.py", "operations/recovery_contract.py",
    "private/compose.json", "private/bff.json", "private/operator.json",
    "private/secrets.json", "private/credentials.json", "private/realm.json",
    "private/proxy.conf", "private/ca.crt", "private/ca.key",
    "private/tls.crt", "private/tls.key", "inputs/images.json",
)


class ContractError(ValueError):
    """Diagnostics contain neither credentials nor business records."""


def require(value, message):
    if not value:
        raise ContractError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    from bson import json_util
    return json_util.dumps(value, sort_keys=True,
                           json_options=json_util.CANONICAL_JSON_OPTIONS)


def mongo_facts(db):
    result = {}
    for collection in db.list_collections():
        name = collection["name"]
        rows = sorted(canonical(row) for row in db[name].find({}))
        indexes = sorted(({**dict(i), "key": list(i["key"].items())}
                          for i in db[name].list_indexes()), key=lambda i: i["name"])
        result[name] = {"count": len(rows), "data": sha("\n".join(rows).encode()),
                        "options": json.loads(canonical(collection.get("options", {}))),
                        "indexes": json.loads(canonical(indexes))}
    return result


def mongo_preconditions(db):
    """Accept complete v4/v6 only; never repair partial histories or pending work."""
    rows = list(db[HISTORY].find({}, {"_id": 1, "checksum": 1, "status": 1}).sort("_id", 1))
    require(len(rows) in {4, 6}, "Incomplete or unsupported migration history.")
    for number, row in enumerate(rows, 1):
        require(type(row["_id"]) is int and row["_id"] == number
                and row.get("status") == "succeeded"
                and row.get("checksum") == CHECKSUMS[number - 1],
                "Migration history checksum/status/version mismatch.")
    from stockpilot.infrastructure.persistence.mongodb.schema_migrations import MigrationError, MigrationRunner
    try:
        MigrationRunner(db).require_version(len(rows))  # validate only; never apply/DDL
    except MigrationError:
        raise ContractError("Migration structure validation failed.") from None
    names = set(db.list_collection_names())
    require((NEW_COLLECTIONS <= names) if len(rows) == 6 else not (NEW_COLLECTIONS & names),
            "Migration history and v6 inventory differ.")
    require(db.stockpilot_schema_migration_locks.count_documents({}) == 0,
            "Migration lock present; capture refused.")
    # ORDER, personal archive and Contact receipts commit atomically and have
    # no status field. Only these physical-operation journals can be pending.
    for name in ("b2b_stock_operations", "b2b_product_archives"):
        require(db[name].count_documents({"status": "pending"}) == 0,
                "Pending operation requires reconciliation.")
    return {"version": len(rows), "checksums": list(CHECKSUMS[:len(rows)])}


def postgres_facts(connection):
    """Hash logical live-column order; never compare physical attnum/OIDs."""
    from psycopg import sql
    c = connection
    c.execute("SET TRANSACTION READ ONLY")
    require(c.execute("SELECT count(*) FROM pg_prepared_xacts").fetchone()[0] == 0,
            "Prepared PostgreSQL transaction present.")
    result = {}
    tables = c.execute("SELECT schemaname,tablename FROM pg_tables "
                       "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
                       "ORDER BY 1,2").fetchall()
    if ("stockpilot_b2b", "assistant_turns") in tables:
        require(c.execute("SELECT count(*) FROM stockpilot_b2b.assistant_turns "
                          "WHERE status='pending'").fetchone()[0] == 0,
                "Pending assistant turn requires reconciliation.")
    for schema, table in tables:
        # PostgreSQL canonical text avoids converting numeric values through float.
        rows = [row[0] for row in c.execute(sql.SQL(
            "SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text"
        ).format(sql.Identifier(schema, table)))]
        columns = c.execute(
            "SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,"
            "pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated,"
            "co.collname,cn.nspname FROM pg_attribute a "
            "JOIN pg_class t ON t.oid=a.attrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "LEFT JOIN pg_attrdef d ON d.adrelid=t.oid AND d.adnum=a.attnum "
            "LEFT JOIN pg_collation co ON co.oid=a.attcollation "
            "LEFT JOIN pg_namespace cn ON cn.oid=co.collnamespace "
            "WHERE n.nspname=%s AND t.relname=%s AND a.attnum>0 AND NOT a.attisdropped "
            "ORDER BY a.attnum", (schema, table)).fetchall()
        indexes = c.execute("SELECT indexname,indexdef FROM pg_indexes "
                            "WHERE schemaname=%s AND tablename=%s ORDER BY 1", (schema, table)).fetchall()
        constraints = c.execute(
            "SELECT conname,pg_get_constraintdef(x.oid),convalidated FROM pg_constraint x "
            "JOIN pg_class t ON t.oid=x.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND t.relname=%s ORDER BY 1", (schema, table)).fetchall()
        grants = c.execute(
            "SELECT grantee,privilege_type,is_grantable FROM information_schema.table_privileges "
            "WHERE table_schema=%s AND table_name=%s ORDER BY 1,2,3", (schema, table)).fetchall()
        owner = c.execute("SELECT tableowner FROM pg_tables WHERE schemaname=%s AND tablename=%s",
                          (schema, table)).fetchone()[0]
        result[schema + "." + table] = {
            "count": len(rows), "data": sha("\n".join(rows).encode()),
            "structure": sha(json.dumps([columns, indexes, constraints, grants, owner],
                                        default=str).encode()),
        }
    sequences = []
    for row in c.execute(
        "SELECT schemaname,sequencename,data_type,start_value,min_value,max_value,"
        "increment_by,cycle,cache_size FROM pg_sequences "
        "WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2"
    ):
        state = c.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(
            sql.Identifier(row[0], row[1]))).fetchone()
        sequences.append([*row, *state])
    result["_sequences"] = sha(json.dumps(sequences, default=str).encode())
    result["_roles"] = {row[0]: list(row[1:]) for row in c.execute(
        "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
        "rolreplication,rolconnlimit,rolbypassrls,rolvaliduntil::text FROM pg_roles ORDER BY 1")}
    result["_memberships"] = [list(row) for row in c.execute(
        "SELECT r.rolname,m.rolname,a.admin_option FROM pg_auth_members a "
        "JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member ORDER BY 1,2")]
    result["_extensions"] = [list(row) for row in c.execute(
        "SELECT e.extname,e.extversion,n.nspname FROM pg_extension e "
        "JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1")]
    return result


def validate_capture(facts):
    require(set(facts) == {"mongo", "application", "keycloak", "schema", "excluded_ephemeral"},
            "Incomplete capture scopes.")
    version = facts["schema"].get("version")
    require(type(version) is int and version in {4, 6} and facts["schema"].get("checksums") == list(CHECKSUMS[:version]),
            "Invalid captured schema history.")
    require(HISTORY in facts["mongo"] and facts["mongo"][HISTORY].get("count") == version,
            "Migration register missing.")
    require(version != 6 or NEW_COLLECTIONS <= set(facts["mongo"]), "v6 inventory incomplete.")
    require(set(facts["excluded_ephemeral"]) <= EPHEMERAL, "Unsafe ephemeral exclusion.")
    for value in facts["mongo"].values():
        require(set(value) == {"count", "data", "options", "indexes"}
                and type(value["count"]) is int and value["count"] >= 0
                and re.fullmatch("[0-9a-f]{64}", value["data"]) is not None
                and isinstance(value["options"], dict) and isinstance(value["indexes"], list),
                "Incomplete Mongo structure proof.")
    for scope in ("application", "keycloak"):
        tables = facts[scope]
        require({"_roles", "_memberships", "_extensions", "_sequences"} <= set(tables),
                "Incomplete PostgreSQL metadata proof.")
        for name, value in tables.items():
            if not name.startswith("_"):
                require(set(value) == {"count", "data", "structure"}
                        and type(value["count"]) is int and value["count"] >= 0
                        and all(re.fullmatch("[0-9a-f]{64}", value[k]) for k in ("data", "structure")),
                        "Incomplete PostgreSQL table proof.")
    return True


def compare_capture(expected, actual, *, restore_role=None):
    validate_capture(expected)
    validate_capture(actual)
    require(set(expected) == set(actual), "Capture scopes differ.")
    exclusions = expected.get("excluded_ephemeral", [])
    require(set(exclusions) <= EPHEMERAL and exclusions == actual.get("excluded_ephemeral", []),
            "Unsafe ephemeral exclusion.")
    for scope in ("mongo", "application", "keycloak"):
        e, a = expected[scope], actual[scope]
        require(set(e) == set(a), "Collection/table inventory differs.")
        for name in e:
            left, right = e[name], a[name]
            if scope == "mongo" and name in exclusions:
                left = {k: v for k, v in left.items() if k not in {"count", "data"}}
                right = {k: v for k, v in right.items() if k not in {"count", "data"}}
            if name == "_roles" and restore_role is not None:
                require(restore_role not in left and restore_role in right,
                        "Restore bootstrap role provenance ambiguous.")
                require(right[restore_role] == BOOTSTRAP_ROLE_ATTRIBUTES,
                        "Unexpected restore bootstrap role attributes.")
                right = {k: v for k, v in right.items() if k != restore_role}
            require(left == right, "Logical data or structure differs.")
    for key in set(expected) - {"mongo", "application", "keycloak"}:
        require(expected[key] == actual[key], "Capture metadata differs.")
    return True


def checked_file(root, relative):
    root = Path(root).absolute()
    require(root.resolve() == root, "Symlink/relative traversal in root.")
    require(not root.is_symlink() and root.is_dir(), "Invalid root directory.")
    path = root / relative
    require(not Path(relative).is_absolute() and ".." not in Path(relative).parts,
            "Unsafe archive path.")
    cursor = path
    while cursor != root:
        require(not cursor.is_symlink(), "Symlink refused.")
        cursor = cursor.parent
    require(path.is_file(), "Required file missing.")
    return path


def source_provenance(root):
    return {name: file_sha(checked_file(root, name)) for name in PROVENANCE_PATHS}


def verify_backup(directory, expected_manifest_sha, *, installation, active_root):
    require(re.fullmatch("[0-9a-f]{64}", expected_manifest_sha) is not None,
            "Independent manifest digest required.")
    directory = Path(directory)
    manifest = checked_file(directory, "manifest.json")
    require(file_sha(manifest) == expected_manifest_sha, "Manifest digest mismatch.")
    data = json.loads(manifest.read_text())
    require(data.get("format") == FORMAT and data.get("installation") == installation,
            "Backup format/installation mismatch.")
    require(set(data.get("archives", {})) == ARCHIVES, "Required archive set differs.")
    for name, expected in data["archives"].items():
        path = checked_file(directory, name)
        require(type(expected.get("bytes")) is int and expected["bytes"] > 0
                and path.stat().st_size == expected["bytes"]
                and file_sha(path) == expected.get("sha256"), "Archive integrity failure.")
    provenance = data.get("provenance", {})
    require(set(provenance) == set(PROVENANCE_PATHS), "Incomplete operator/configuration provenance.")
    require(source_provenance(active_root) == provenance, "Active configuration/operator drift.")
    with tarfile.open(directory / "configuration.tar.gz", "r:gz") as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        require(len(names) == len(set(names)), "Duplicate archive member.")
        for name in PROVENANCE_PATHS:
            member = archive.getmember(name)
            require(member.isfile() and not member.issym() and not member.islnk()
                    and 0 <= member.size <= 4 * 1024 * 1024, "Unsafe configuration member.")
            require(sha(archive.extractfile(member).read()) == provenance[name],
                    "Archived configuration/operator provenance mismatch.")
    facts = json.loads((directory / "facts.json").read_text())
    require(facts.get("schema", {}).get("version") in {4, 6}, "Schema capture missing.")
    version = facts["schema"]["version"]
    require(facts["schema"].get("checksums") == list(CHECKSUMS[:version])
            and HISTORY in facts.get("mongo", {})
            and facts["mongo"][HISTORY].get("count") == version, "Schema capture mismatch.")
    require(version != 6 or NEW_COLLECTIONS <= set(facts["mongo"]), "v6 collections missing.")
    validate_capture(facts)
    require(data.get("schema") == facts["schema"], "Manifest and captured schema differ.")
    images = data.get("images", {})
    require(set(images) == {"api", "web", "mongo", "postgres", "keycloak-db", "keycloak"}
            and all(isinstance(v, str) and re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", v) for v in images.values()),
            "Unpinned or incomplete image provenance.")
    require(json.loads(checked_file(active_root, "inputs/images.json").read_text()) == images,
            "Image manifest differs from active configuration.")
    return data


def operation_lock(path):
    """Retain this FD until process exit. Never unlink/steal an existing lock."""
    import fcntl
    path = Path(path).absolute()
    require(path.parent.is_dir() and path.parent.resolve() == path.parent,
            "Unsafe recovery lock parent.")
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                "Unsafe recovery lock file.")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd
