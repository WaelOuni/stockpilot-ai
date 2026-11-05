"""Admission failures must never reach the production transition."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from scripts.ops import execute_v024_migration as host
from scripts.ops import v024_installation_migration as migration

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def plan():
    return {
        "format": "stockpilot-v024-migration-plan-v1", "installation": migration.INSTALLATION,
        "ssh_fingerprint": host.SSH_FINGERPRINT, "authorization": host.AUTHORIZATION,
        "approved_sha": "a" * 40, "authorized_at": (NOW - timedelta(minutes=10)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=60)).isoformat(),
        "external_writers_checked_at": (NOW - timedelta(minutes=1)).isoformat(),
        "compose_sha256": "1" * 64, "operator_config_sha256": "2" * 64,
        "code_sha256": {n: "3" * 64 for n in host.CODE},
        "backup": "/opt/stockpilot/backups/synthetic", "manifest_sha256": "4" * 64,
        "restore_proof": "/opt/stockpilot/backups/synthetic/restore.json",
        "restore_sha256": "5" * 64, "runtime_image": "sha256:" + "6" * 64,
        "rollback_context_sha256": "7" * 64, "docker_engine": "synthetic-engine",
    }


@pytest.mark.parametrize("field,value", [
    ("format", "legacy"), ("authorization", "anything"), ("ssh_fingerprint", "different"),
    ("installation", "other"), ("approved_sha", "main"), ("runtime_image", "candidate:latest"),
    ("manifest_sha256", ""), ("restore_sha256", "f" * 63), ("code_sha256", {}),
    ("expires_at", (NOW - timedelta(seconds=1)).isoformat()),
    ("authorized_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("external_writers_checked_at", (NOW - timedelta(minutes=11)).isoformat()),
    ("authorized_at", "2026-11-05T11:50:00"),
])
def test_invalid_plan_refused(field, value):
    value_plan = plan()
    host.plan_shape(value_plan, NOW)
    value_plan[field] = value
    with pytest.raises(migration.TransitionRefused):
        host.plan_shape(value_plan, NOW)


def restore():
    return {
        "captured_at": "20261105T115500Z", "database": migration.DATABASE,
        "vm_installation_id": migration.INSTALLATION, "schema": {"version": 4},
        "source_ids": {"mongo": "source-m", "test-postgres": "source-p", "keycloak-db": "source-k"},
    }, {
        "status": "logical_restore_verified", "backup": plan()["backup"], "schema_version": 4,
        "all_counts_and_hashes_equal": True, "all_structures_equal": True,
        "source_archives_unchanged": True, "no_published_ports": True,
        "restored_at": "2026-11-05T11:59:00+00:00",
        "restore_ids": {"mongo": "restore-m", "test-postgres": "restore-p", "keycloak-db": "restore-k"},
    }


@pytest.mark.parametrize("change", ["source-overlap", "missing-db", "old-backup", "wrong-version", "failed", "ports", "drift"])
def test_restore_proof_must_be_fresh_complete_and_isolated(change):
    manifest, proof = restore()
    host.restore_admission(plan(), manifest, proof, NOW)
    if change == "source-overlap":
        proof["restore_ids"]["mongo"] = manifest["source_ids"]["mongo"]
    elif change == "missing-db":
        del proof["restore_ids"]["keycloak-db"]
    elif change == "old-backup":
        manifest["captured_at"] = "20261103T115500Z"
    elif change == "wrong-version":
        manifest["schema"]["version"] = 6
    elif change == "failed":
        proof["all_structures_equal"] = False
    elif change == "ports":
        proof["no_published_ports"] = False
    else:
        proof["backup"] += "-other"
    with pytest.raises(migration.TransitionRefused):
        host.restore_admission(plan(), manifest, proof, NOW)


def test_private_file_refuses_symlink_and_escape(tmp_path):
    target = tmp_path / "source"
    target.write_text("synthetic")
    link = tmp_path / "link"
    link.symlink_to(target)
    for path in (link, tmp_path.parent / "foreign", tmp_path / ".." / "foreign"):
        with pytest.raises(migration.TransitionRefused):
            host.private_file(path, tmp_path)


def test_sources_require_stopped_writers_original_volumes_and_no_unknown_client(monkeypatch):
    manifest = {"source_ids": {}, "source_volumes": {}}
    records = []
    for name in sorted(host.SERVICES):
        cid = "synthetic-" + name
        manifest["source_ids"][name] = cid
        volumes = [] if name in {"bff", "proxy", "keycloak"} else [name + "-data"]
        manifest["source_volumes"][name] = volumes
        records.append({"Id": cid, "Config": {"Labels": {
            "com.docker.compose.service": name, "com.docker.compose.project": host.PROJECT,
            "stockpilot.installation": migration.INSTALLATION.replace("-", "")}},
            "State": {"Status": "exited" if name in {"bff", "keycloak"} else "running",
                      "Running": name not in {"bff", "keycloak"}, "Health": {"Status": "healthy"}},
            "Mounts": [{"Name": v, "Type": "volume"} for v in volumes], "HostConfig": {"PortBindings": {}}})
    clients = {r["Id"]: {} for r in records if r["State"]["Running"]}
    def command(args):
        if "compose" in args:
            return " ".join(manifest["source_ids"].values()).encode()
        return json.dumps([{"Containers": clients}] if "network" in args else records).encode()
    monkeypatch.setattr(host, "command", command)
    assert set(host.inspect_sources(manifest)) == host.SERVICES
    baseline = deepcopy(records)
    for change in ("writer", "volume", "identity", "port", "health", "external"):
        records[:] = deepcopy(baseline)
        clients.clear()
        clients.update({r["Id"]: {} for r in records if r["State"]["Running"]})
        if change == "writer":
            records[0]["State"].update(Status="running", Running=True)
        elif change == "volume":
            next(r for r in records if r["Mounts"])["Mounts"][0]["Name"] = "foreign"
        elif change == "identity":
            records[0]["Id"] = "replaced"
        elif change == "port":
            next(r for r in records if r["Mounts"])["HostConfig"]["PortBindings"] = {"27017": [{}]}
        elif change == "health":
            next(r for r in records if r["State"]["Running"])["State"]["Health"]["Status"] = "unhealthy"
        else:
            clients["unknown-writer"] = {}
        with pytest.raises(migration.TransitionRefused):
            host.inspect_sources(manifest)


def test_failed_admission_never_creates_or_starts_container(tmp_path, monkeypatch):
    path = tmp_path / "evidence" / "plan.json"
    path.parent.mkdir()
    path.write_text(json.dumps(plan()))
    monkeypatch.setattr(host, "ROOT", tmp_path)
    monkeypatch.setattr(host, "private_file", lambda p: Path(p))
    monkeypatch.setattr(host.contract, "operation_lock", lambda p: 12345)
    closed = []
    monkeypatch.setattr(host.os, "close", closed.append)
    def reject(_):
        raise migration.TransitionRefused("synthetic refusal")
    monkeypatch.setattr(host, "admit", reject)
    monkeypatch.setattr(host, "command", lambda _: pytest.fail("Docker mutation after failed admission"))
    monkeypatch.setattr(host.sys, "argv", ["operator", str(path)])
    with pytest.raises(migration.TransitionRefused):
        host.main()
    assert closed == [12345]


def test_guarded_disposable_cli_is_unchanged():
    # Production transition must use its own reviewed finite state machine.
    source = Path(migration.__file__).read_text()
    assert ".apply(" not in source and "._execute(" not in source
    assert "db.drop" not in source and "collMod" not in source
