"""Quiescent, paired backup/restore of independently attested disposable resources.

Not a production backup utility. Only called by the canonical validation owner.
Archive bytes are sensitive; manifests prove transport integrity, not authenticity
against an attacker controlling the local Docker engine or runtime directory.
"""

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime

from bson import json_util

SERVICES = {
    "mongo",
    "test-postgres",
    "keycloak-db",
    "keycloak",
    "bff",
    "proxy",
    "auth-mongo",
}
ARCHIVES = {
    "mongo": "mongo.archive.gz",
    "test-postgres": "application.dump",
    "keycloak-db": "keycloak.dump",
}


def attest(owner):
    records = {}
    ids = owner.command(
        [*owner.compose, "--profile", "*", "ps", "-aq"], owner.env
    ).split()
    if not ids:
        raise RuntimeError("Owned project absent.")
    for cid in ids:
        item = json.loads(owner.command(["docker", "inspect", cid]))[0]
        labels = item["Config"].get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if (
            labels.get("com.docker.compose.project") != owner.env["BFF_TEST_PROJECT"]
            or labels.get("stockpilot.audit.purpose") != "v1384-ephemeral"
            or str(owner.compose[3])
            not in labels.get("com.docker.compose.project.config_files", "")
            or service not in SERVICES
            or service in records
        ):
            raise RuntimeError("Disposable provenance mismatch.")
        for mount in item.get("Mounts", []):
            owner.validate_mount(mount)
        for values in (item["HostConfig"].get("PortBindings") or {}).values():
            if not all(v["HostIp"] == "127.0.0.1" for v in values):
                raise RuntimeError("Nonlocal management binding.")
        records[service] = item
    return records


def binary(owner, service, arguments, destination=None, source=None):
    record = attest(owner)[service]
    if record["State"]["Status"] != "running":
        raise RuntimeError("Backup/restore database unavailable.")
    args = ["docker", "exec"] + (["-i"] if source else []) + [record["Id"], *arguments]
    # No shell, no archive-provided commands/options/paths, no credentials in argv.
    with destination.open("xb") if destination else open("/dev/null", "wb") as output:
        incoming = source.open("rb") if source else None
        try:
            result = subprocess.run(
                args,
                stdin=incoming,
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("Private database archive operation failed.")
        finally:
            if incoming:
                incoming.close()
    if destination:
        destination.chmod(0o600)


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Regular private archive required.")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def mongo_facts(db):
    result = {}
    for item in db.list_collections():
        name = item["name"]
        indexes = sorted(
            (dict(i) for i in db[name].list_indexes()), key=lambda i: i["name"]
        )
        documents = sorted(
            json_util.dumps(d, sort_keys=True) for d in db[name].find({})
        )
        result[name] = {
            "count": len(documents),
            "indexes": indexes,
            "options": item.get("options", {}),
            "data": hashlib.sha256("\n".join(documents).encode()).hexdigest(),
        }
    return result


def pg_facts(owner, service):
    cid = attest(owner)[service]["Id"]
    user, database, port = (
        ("ci", "stockpilot_ci", "55432")
        if service == "test-postgres"
        else ("keycloak_test", "keycloak_test", "5432")
    )
    base = [
        "docker",
        "exec",
        cid,
        "psql",
        "-X",
        "-U",
        user,
        "-d",
        database,
        "-p",
        port,
        "-At",
        "-c",
    ]
    tables = owner.command(
        [
            *base,
            (
                "SELECT table_schema||'.'||table_name FROM information_schema.tables "
                "WHERE table_type='BASE TABLE' AND table_schema NOT IN "
                "('pg_catalog','information_schema') ORDER BY 1"
            ),
        ]
    ).splitlines()
    facts = {}
    for name in tables:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", name):
            raise RuntimeError("Unexpected archive schema.")
        rows = sorted(
            owner.command(
                [*base, "SELECT row_to_json(t)::text FROM " + name + " t"]
            ).splitlines()
        )
        facts[name] = {
            "count": len(rows),
            "data": hashlib.sha256("\n".join(rows).encode()).hexdigest(),
        }
    return facts


def paired_backup(owner, db, directory, *, expected_pending):
    if not re.fullmatch(r"stockpilot_b2b_test_[a-f0-9]{32}", db.name):
        raise RuntimeError("Disposable database required.")
    records = attest(owner)
    if any(records[n]["State"]["Running"] for n in ("proxy", "bff", "keycloak")):
        raise RuntimeError(
            "Ingress and all application writers must be stopped and drained."
        )
    # Caller checked the durable Mongo outcome of each explicitly recorded pending action.
    # No unknown pending is silently called a coherent completed workflow.
    pending = pg_pending(owner)
    if pending != expected_pending:
        raise RuntimeError(
            "Pending journal changed; reconcile or explicitly inspect it."
        )
    before = {
        "mongo": mongo_facts(db),
        "application": pg_facts(owner, "test-postgres"),
        "keycloak": pg_facts(owner, "keycloak-db"),
    }
    directory.mkdir(mode=0o700)
    binary(
        owner,
        "mongo",
        ["mongodump", "--quiet", "--db", db.name, "--archive", "--gzip"],
        destination=directory / ARCHIVES["mongo"],
    )
    for service, user, database, port in (
        ("test-postgres", "ci", "stockpilot_ci", "55432"),
        ("keycloak-db", "keycloak_test", "keycloak_test", "5432"),
    ):
        binary(
            owner,
            service,
            [
                "pg_dump",
                "-U",
                user,
                "-d",
                database,
                "-p",
                port,
                "--format=custom",
                "--no-owner",
                "--no-privileges",
            ],
            destination=directory / ARCHIVES[service],
        )
    after = {
        "mongo": mongo_facts(db),
        "application": pg_facts(owner, "test-postgres"),
        "keycloak": pg_facts(owner, "keycloak-db"),
    }
    if before != after:
        raise RuntimeError("Data changed during backup; snapshot refused.")
    manifest = {
        "format": 1,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "mongo_database": db.name,
        "quiescence": "owned ingress/BFF/Keycloak stopped; logical before/after equal",
        "pending_actions": pending,
        "facts": before,
        "source_project": owner.env["BFF_TEST_PROJECT"],
        "source_ids": {n: i["Id"] for n, i in records.items()},
        "images": {n: i["Image"] for n, i in records.items()},
        "archives": {
            n: {
                "sha256": digest(directory / filename),
                "bytes": (directory / filename).stat().st_size,
            }
            for n, filename in ARCHIVES.items()
        },
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str)
    )
    (directory / "manifest.json").chmod(0o600)
    return manifest


def pg_pending(owner):
    cid = attest(owner)["test-postgres"]["Id"]
    rows = owner.command(
        [
            "docker",
            "exec",
            cid,
            "psql",
            "-X",
            "-U",
            "ci",
            "-p",
            "55432",
            "-d",
            "stockpilot_ci",
            "-At",
            "-c",
            (
                "SELECT turn_id FROM stockpilot_b2b.assistant_turns "
                "WHERE status='pending' ORDER BY turn_id"
            ),
        ]
    ).splitlines()
    return rows


def restore(owner, db, directory, manifest):
    if not isinstance(db.name, str) or not re.fullmatch(
        r"stockpilot_b2b_test_[a-f0-9]{32}", db.name
    ):
        raise RuntimeError("Disposable restore namespace required.")
    if db.name != manifest.get("mongo_database"):
        raise RuntimeError("Explicit restore namespace mismatch.")
    records = attest(owner)
    if (
        owner.env["BFF_TEST_PROJECT"] == manifest["source_project"]
        or any(i["Id"] in manifest["source_ids"].values() for i in records.values())
        or any(n in records for n in ("bff", "keycloak", "proxy"))
        or db.list_collection_names()
        or pg_facts(owner, "test-postgres")
        or pg_facts(owner, "keycloak-db")
    ):
        raise RuntimeError("Independent fresh empty restore targets required.")
    for service, user, database, port in (
        ("test-postgres", "ci", "stockpilot_ci", "55432"),
        ("keycloak-db", "keycloak_test", "keycloak_test", "5432"),
    ):
        databases = owner.command(
            [
                "docker",
                "exec",
                records[service]["Id"],
                "psql",
                "-X",
                "-U",
                user,
                "-d",
                database,
                "-p",
                port,
                "-At",
                "-c",
                "SELECT datname FROM pg_database",
            ]
        ).splitlines()
        if set(databases) - {"postgres", "template0", "template1", database}:
            raise RuntimeError("Restore PostgreSQL server is not dedicated and empty.")
    # Empty Docker-owned instance, not merely an empty database on a shared server.
    names = set(db.client.list_database_names())
    if names - {"admin", "config", "local"}:
        raise RuntimeError("Restore Mongo server is not empty.")
    for service, filename in ARCHIVES.items():
        if records[service]["Image"] != manifest["images"][service]:
            raise RuntimeError("Restore image compatibility mismatch.")
        if digest(directory / filename) != manifest["archives"][service]["sha256"]:
            raise RuntimeError("Archive checksum mismatch.")
    binary(
        owner,
        "mongo",
        [
            "mongorestore",
            "--quiet",
            "--archive",
            "--gzip",
            "--nsInclude=" + db.name + ".*",
        ],
        source=directory / ARCHIVES["mongo"],
    )
    for service, user, database, port in (
        ("test-postgres", "ci", "stockpilot_ci", "55432"),
        ("keycloak-db", "keycloak_test", "keycloak_test", "5432"),
    ):
        binary(
            owner,
            service,
            [
                "pg_restore",
                "-U",
                user,
                "-d",
                database,
                "-p",
                port,
                "--single-transaction",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
            ],
            source=directory / ARCHIVES[service],
        )
    facts = {
        "mongo": mongo_facts(db),
        "application": pg_facts(owner, "test-postgres"),
        "keycloak": pg_facts(owner, "keycloak-db"),
    }
    if facts != manifest["facts"]:
        raise RuntimeError("Restored logical data/indexes/options mismatch.")
    return {
        "all_collections_rows_indexes_equal": True,
        "source_project": manifest["source_project"],
        "restore_project": owner.env["BFF_TEST_PROJECT"],
        "physical_container_ids_distinct": True,
        "archive_bytes": sum(a["bytes"] for a in manifest["archives"].values()),
    }
