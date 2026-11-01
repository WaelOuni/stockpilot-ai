"""Offline synthetic identities and temporary files; no VM or Docker access."""

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts import b2b_vm_backup_identity as identity


@pytest.fixture
def scenario():
    containers = {}
    for index, service in enumerate(sorted(identity.SERVICES)):
        containers[service] = {
            "id": str(index) * 64,
            "image": "sha256:" + str(index) * 64,
            "name": "/owned-" + service,
            "mounts": [{"Type": "volume", "Name": "owned-data"}] if service == "mongo" else [],
            "config": "a" * 64, "host": "b" * 64,
            "networks": {"owned": {"NetworkID": "network-original"}},
        }
    history = {
        "source_project": identity.PROJECT,
        "source_ids": {s: c["id"] for s, c in containers.items()},
        "images": {s: c["image"] for s, c in containers.items()},
        "source_volumes": {s: [m["Name"] for m in c["mounts"]] for s, c in containers.items()},
    }
    containers["bff"]["id"] = "f" * 64
    snapshot = {
        "containers": containers,
        "volumes": {"owned-data": {"CreatedAt": "original", "filesystem": [1, 2, 3, 4, 448]}},
        "networks": {"owned": {"Id": "network-original"}},
        "files": {"bff.json": {"sha256": "c" * 64}},
    }
    receipt = {
        "verified": True, "source": identity.SOURCE, "renewal_enabled": False,
        "bff_id": containers["bff"]["id"], "proxy_id": containers["proxy"]["id"],
    }
    return history, snapshot, receipt


def test_recreation_preserves_history_and_requires_external_attestation(scenario):
    history, snapshot, receipt = scenario
    before = copy.deepcopy(scenario)
    result = identity.reconcile(*scenario)
    assert scenario == before
    assert result["snapshot"] == snapshot
    snapshot["containers"]["bff"]["id"] = "unattested"
    assert result["snapshot"]["containers"]["bff"]["id"] == "f" * 64
    with pytest.raises(identity.Refused, match="bff_not_attested"):
        identity.reconcile(history, snapshot, receipt)


@pytest.mark.parametrize("attack", [
    "other_id", "image", "volume", "missing", "duplicate_id", "foreign_history",
    "attestation_failed", "revision", "proxy_receipt", "renewal", "unchanged_bff",
])
def test_rejects_unqualified_transitions(scenario, attack):
    history, snapshot, receipt = scenario
    c = snapshot["containers"]
    if attack == "other_id": c["mongo"]["id"] = "e" * 64
    elif attack == "image": c["bff"]["image"] = "sha256:" + "e" * 64
    elif attack == "volume": c["mongo"]["mounts"][0]["Name"] = "replacement"
    elif attack == "missing": del c["keycloak"]
    elif attack == "duplicate_id": c["proxy"]["id"] = c["bff"]["id"]
    elif attack == "foreign_history": history["source_project"] = "other"
    elif attack == "attestation_failed": receipt["verified"] = False
    elif attack == "revision": receipt["source"] = "e" * 40
    elif attack == "proxy_receipt": receipt["proxy_id"] = "e" * 64
    elif attack == "renewal": receipt["renewal_enabled"] = True
    elif attack == "unchanged_bff": c["bff"]["id"] = history["source_ids"]["bff"]
    with pytest.raises(identity.Refused): identity.reconcile(*scenario)


@pytest.mark.parametrize("section", ["host", "config", "mounts", "network_id", "volume_inode", "file", "extra_file"])
def test_every_inventory_read_rechecks_immutable_resources(scenario, section):
    history, snapshot, receipt = scenario
    generation = identity.reconcile(*scenario)
    calls = []
    reader = identity.guarded_records(
        generation, history, observer=lambda: ({"healthy": True}, copy.deepcopy(snapshot)),
        attester=lambda: calls.append("normal") or receipt,
    )
    assert reader() == {"healthy": True}
    c = snapshot["containers"]["bff"]
    if section in {"host", "config"}: c[section] = "changed"
    elif section == "mounts": c["mounts"].append({"Type": "bind", "RW": True})
    elif section == "network_id": snapshot["networks"]["owned"]["Id"] = "recreated"
    elif section == "volume_inode": snapshot["volumes"]["owned-data"]["filesystem"][1] += 1
    elif section == "file": snapshot["files"]["bff.json"]["sha256"] = "changed"
    elif section == "extra_file": snapshot["files"]["extra.key"] = {}
    with pytest.raises(identity.Refused, match="runtime_generation_changed"): reader()
    assert calls == ["normal"]


def test_failed_attestation_prevents_inventory_and_remains_fail_closed(scenario):
    history, _, _ = scenario
    def deny(): raise identity.Refused("normal_gate_failed")
    def forbidden(): pytest.fail("inventory must not be admitted")
    reader = identity.guarded_records(identity.reconcile(*scenario), history, forbidden, deny)
    for _ in range(2):
        with pytest.raises(identity.Refused, match="normal_gate_failed"): reader()


@pytest.fixture
def private_directory(tmp_path, monkeypatch):
    # pytest's system-owned parent differs on CI. File and lock protections are
    # real; parent ownership is independently exercised below.
    monkeypatch.setattr(identity, "safe_parents", lambda _: None)
    tmp_path.chmod(0o700)
    return tmp_path


def test_generation_is_private_immutable_and_digest_bound(private_directory, scenario):
    path = private_directory / "generation.json"
    value = identity.reconcile(*scenario)
    checksum = identity.write_generation(path, value)
    assert path.stat().st_mode & 0o777 == 0o600
    assert identity.load_generation(path, checksum) == value
    original = path.read_bytes()
    with pytest.raises(FileExistsError): identity.write_generation(path, value)
    assert path.read_bytes() == original
    with pytest.raises(identity.Refused, match="digest_changed"):
        identity.load_generation(path, "0" * 64)
    path.chmod(0o640)
    with pytest.raises(identity.Refused, match="generation_permissions_changed"):
        identity.load_generation(path, checksum)
    path.chmod(0o600)
    path.write_bytes(original + b" ")
    with pytest.raises(identity.Refused, match="digest_changed"):
        identity.load_generation(path, checksum)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "fifo"])
def test_private_metadata_rejects_unsafe_files(private_directory, kind):
    path = private_directory / "file.json"
    source = private_directory / "source"
    source.write_text("sensitive fixture")
    source.chmod(0o600)
    if kind == "symlink": path.symlink_to(source)
    elif kind == "hardlink": os.link(source, path)
    elif kind == "fifo": os.mkfifo(path, 0o600)
    else:
        path.write_text("public"); path.chmod(0o644)
    with pytest.raises((OSError, identity.Refused)): identity.private_bytes(path)
    assert source.read_text() == "sensitive fixture"


def test_lock_excludes_another_operator_and_refuses_links(private_directory):
    path = private_directory / "operator.lock"
    with identity.locked(path):
        with pytest.raises(BlockingIOError):
            with identity.locked(path): pytest.fail("second writer entered")
    with identity.locked(path): pass
    link = private_directory / "linked.lock"
    link.symlink_to(path)
    with pytest.raises(OSError):
        with identity.locked(link): pytest.fail("followed lock symlink")


def test_unsafe_parent_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "generation.json"
    parent_type = type(tmp_path)
    def metadata(_):
        return SimpleNamespace(st_mode=0o40777, st_uid=os.geteuid())
    monkeypatch.setattr(parent_type, "lstat", metadata)
    with pytest.raises(identity.Refused, match="writable_parent"): identity.safe_parents(path)


def test_capture_retains_original_entrypoint_and_guard_order(private_directory, scenario, monkeypatch):
    history, snapshot, receipt = scenario
    generation = identity.reconcile(*scenario)
    events = []
    legacy = private_directory / "backup_operator.py"
    legacy.write_text("# synthetic operator, not the installed VM package\n")
    legacy.chmod(0o600)
    monkeypatch.setattr(identity, "OPERATOR", private_directory)
    monkeypatch.setattr(identity, "verify_packages", lambda: events.append("package"))
    original_records = identity.guarded_records
    monkeypatch.setattr(identity, "guarded_records", lambda g, h: original_records(
        g, h, lambda: ({"six": True}, snapshot), lambda: events.append("attest") or receipt,
    ))
    module = SimpleNamespace(EXPECTED=copy.deepcopy(history["source_ids"]))
    lock_path = private_directory / "capture.lock"
    def main():
        assert identity.sys.argv[-1] == "capture-authorized-v13133"
        with identity.locked(lock_path):
            events.append("lock")
            assert module.records() == {"six": True}
            events.extend(["normal_preflight", "normal_capture", "normal_recovery"])
            assert module.records() == {"six": True}
    module.main = main
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda _: None))
    monkeypatch.setattr(identity.importlib.util, "spec_from_file_location", lambda *_: spec)
    monkeypatch.setattr(identity.importlib.util, "module_from_spec", lambda _: module)
    original_argv = identity.sys.argv
    identity.invoke_operator("capture-authorized", generation, history)
    assert events == ["package", "lock", "attest", "normal_preflight", "normal_capture", "normal_recovery"]
    assert module.EXPECTED == history["source_ids"]
    assert identity.sys.argv is original_argv
    assert legacy.read_text().startswith("# synthetic")


def test_observation_hashes_secrets_and_rejects_foreign_consumers(private_directory, monkeypatch):
    root = private_directory
    private = root / "private"
    private.mkdir(mode=0o700)
    config = private / "bff.json"
    config.write_text('{"secret":"SYNTHETIC-DO-NOT-EXPORT"}')
    config.chmod(0o600)
    volume = root / "volume"
    volume.mkdir(mode=0o700)
    rows = []
    for i, service in enumerate(sorted(identity.SERVICES)):
        rows.append({
            "Id": str(i) * 64, "Image": "sha256:" + str(i) * 64,
            "Name": "/owned-" + service,
            "Config": {"Labels": {"com.docker.compose.service": service,
                                  "com.docker.compose.project": identity.PROJECT},
                       "Env": ["SECRET=SYNTHETIC-DO-NOT-EXPORT"]},
            "HostConfig": {"ReadonlyRootfs": True},
            "Mounts": [{"Type": "volume", "Name": "data", "Destination": "/data"}] if service == "mongo" else [],
            "NetworkSettings": {"Networks": {"owned": {"NetworkID": "original"}}},
        })
    network = {"Id": "original", "Name": "owned", "Containers": {r["Id"]: {} for r in rows}}
    def fake_docker(*args):
        if args[0] == "ps": return "\n".join(r["Id"] for r in rows).encode()
        if args[0] == "inspect": return json.dumps(rows).encode()
        if args[:2] == ("volume", "inspect"):
            return json.dumps([{"Name": "data", "Mountpoint": str(volume), "CreatedAt": "original"}]).encode()
        if args[:2] == ("network", "inspect"): return json.dumps([network]).encode()
        pytest.fail("unexpected Docker operation")
    monkeypatch.setattr(identity, "ROOT", root)
    monkeypatch.setattr(identity, "docker", fake_docker)
    _, snapshot = identity.observe()
    assert "SYNTHETIC-DO-NOT-EXPORT" not in json.dumps(snapshot)
    assert len(snapshot["containers"]) == 6
    network["Containers"]["foreign"] = {}
    with pytest.raises(identity.Refused, match="foreign_network_consumer"): identity.observe()
    del network["Containers"]["foreign"]
    rows[0]["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
    with pytest.raises(identity.Refused, match="project_changed"): identity.observe()


def test_package_change_is_refused_before_operator_import(private_directory, monkeypatch):
    script = private_directory / "backup_operator.py"
    script.write_text("not executed")
    script.chmod(0o600)
    manifest = private_directory / "package-manifest.json"
    manifest.write_text(json.dumps({"files": {script.name: identity.digest(script.read_bytes())}}))
    manifest.chmod(0o600)
    monkeypatch.setattr(identity, "OPERATOR", private_directory)
    monkeypatch.setattr(identity, "PINS", {manifest: identity.digest(manifest.read_bytes())})
    identity.verify_packages()
    script.write_text("changed")
    with pytest.raises(identity.Refused, match="operator_package_changed"):
        identity.verify_packages()


def test_optimization_is_refused_without_inspection(monkeypatch, capsys):
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    monkeypatch.setattr(identity.os, "geteuid", lambda: 0)
    monkeypatch.setattr(identity.sys, "argv", ["adapter", "plan", "--generation", "/unused.json"])
    monkeypatch.setattr(identity, "command", lambda _: pytest.fail("no inspection allowed"))
    assert identity.main() == 1
    assert "NO-GO" in capsys.readouterr().out


@pytest.fixture
def fresh_capture(private_directory, scenario, monkeypatch):
    monkeypatch.setattr(identity, "ROOT", private_directory)
    backups = private_directory / "backups"
    backups.mkdir(mode=0o700)
    backup = backups / "v13133-before-upgrade-20261031T120000Z"
    backup.mkdir(mode=0o700)
    generation = identity.reconcile(*scenario)
    generation["created_utc"] = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    containers = generation["snapshot"]["containers"]
    manifest = {
        "format": 2, "postgres_backup_format": "pg-full-fidelity-v1",
        "captured_at_utc": datetime.now(UTC).isoformat(), "source_project": identity.PROJECT,
        "source_ids": {s: c["id"] for s, c in containers.items()},
        "images": {s: c["image"] for s, c in containers.items()},
        "source_engine_id": "owned-engine",
        "source_volumes": {s: [m["Name"] for m in c["mounts"] if m["Type"] == "volume"] for s, c in containers.items()},
        "private_file_hashes": {n: f["sha256"] for n, f in generation["snapshot"]["files"].items()},
        "archives": {},
    }
    names = {"mongo.archive.gz", "private-state.tar", "runtime-state.tar", "application-images.tar", "image-archive-proof.json", "image-runtime-proof.json"}
    names |= {f"pg-{db}/{n}" for db in ("application", "keycloak") for n in ("globals.sql", "database.dump", "manifest.json")}
    for name in names:
        path = backup / name
        path.parent.mkdir(mode=0o700, exist_ok=True)
        data = ("synthetic " + name).encode()
        path.write_bytes(data); path.chmod(0o600)
        manifest["archives"][name] = {"sha256": identity.digest(data), "bytes": len(data)}
    result = {"capture_valid": True, "containers_restarted": True, "source_recovered": False, "backup": str(backup)}
    identity.write_generation(backup / "manifest.json", manifest)
    identity.write_generation(backup / "operator-result.json", result)
    return backup, generation, manifest, result


@pytest.mark.parametrize("drift", ["old", "future", "id", "image", "configuration", "incomplete", "failed_restart", "wrong_result_path", "volume"])
def test_post_capture_gate_cannot_admit_historical_or_incomplete_capture(fresh_capture, drift):
    backup, generation, manifest, result = fresh_capture
    if drift == "old": manifest["captured_at_utc"] = "2020-01-01T00:00:00+00:00"
    elif drift == "future": manifest["captured_at_utc"] = "2099-01-01T00:00:00+00:00"
    elif drift == "id": manifest["source_ids"]["bff"] = "historical"
    elif drift == "image": manifest["images"]["bff"] = "other"
    elif drift == "configuration": manifest["private_file_hashes"]["bff.json"] = "other"
    elif drift == "incomplete": result["capture_valid"] = False
    elif drift == "failed_restart": result["containers_restarted"] = False
    elif drift == "volume": manifest["source_volumes"]["mongo"] = ["another-volume"]
    else: result["backup"] = "/old/backup"
    (backup / "manifest.json").write_text(json.dumps(manifest))
    (backup / "operator-result.json").write_text(json.dumps(result))
    with pytest.raises(identity.Refused): identity.capture_metadata(backup, generation)


@pytest.mark.parametrize("drift", ["bytes", "size", "extra", "symlink"])
def test_archive_integrity_is_checked_before_recovery_attestation(fresh_capture, drift):
    backup, _, manifest, _ = fresh_capture
    identity.verify_archives(backup, manifest)
    path = backup / "mongo.archive.gz"
    if drift == "bytes": path.write_bytes(b"corruption")
    elif drift == "size": manifest["archives"][path.name]["bytes"] += 1
    elif drift == "extra": manifest["archives"]["../escape"] = {}
    else:
        path.unlink(); path.symlink_to(backup / "private-state.tar")
    with pytest.raises((identity.Refused, OSError)): identity.verify_archives(backup, manifest)
    assert not (backup / "recovery-attestation.json").exists()


def test_post_capture_records_accept_same_containers_but_not_later_drift(fresh_capture, monkeypatch):
    backup, generation, _, _ = fresh_capture
    snapshot = copy.deepcopy(generation["snapshot"])
    monkeypatch.setattr(identity, "load_operator", lambda: SimpleNamespace())
    monkeypatch.setattr(identity, "observe", lambda: ({"same_containers": True}, snapshot))
    operator = identity.post_capture_operator(backup, generation)
    assert operator.records() == {"same_containers": True}
    snapshot["containers"]["bff"]["id"] = "another-recreation"
    with pytest.raises(identity.Refused, match="runtime_generation_changed"): operator.records()


def test_recovery_writes_proof_only_after_all_original_gates(fresh_capture, monkeypatch):
    backup, generation, _, result = fresh_capture
    before = (backup / "operator-result.json").read_bytes()
    events = []
    rows = {s: {"Id": s, "State": {"Running": True}} for s in identity.SERVICES}
    module = SimpleNamespace(
        records=lambda: rows,
        healthy=lambda _: events.append("healthy"),
        config=lambda: {}, facts=lambda *_: events.append("facts"),
    )
    monkeypatch.setattr(identity, "post_capture_operator", lambda *_: module)
    monkeypatch.setattr(identity, "docker", lambda *_: b"owned-engine")
    monkeypatch.setattr(identity, "https_ready", lambda _: events.append("https"))
    identity.verify_recovery(backup, generation)
    assert events == ["healthy", "healthy", "healthy", "facts", "https"]
    assert json.loads((backup / "operator-result.json").read_bytes())["source_recovered"] is True
    assert json.loads((backup / "operator-result.before-identity-recovery.json").read_bytes()) == json.loads(before)
    proof = json.loads((backup / "recovery-attestation.json").read_bytes())
    assert proof["identity_generation_sha256"] == identity.digest(identity.encoded(generation) + b"\n")
    rows["bff"]["State"]["Running"] = False
    with pytest.raises(identity.Refused, match="source_not_running"): identity.verify_recovery(backup, generation)


def test_restore_requires_generation_bound_source_recovery(fresh_capture, monkeypatch):
    backup, generation, _, result = fresh_capture
    monkeypatch.setattr(identity, "recovery_checks", lambda *_: (SimpleNamespace(), result))
    with pytest.raises(identity.Refused, match="source_recovery_not_attested"):
        identity.restore_capture(backup, generation, None, None, None)
    result["source_recovered"] = True
    identity.write_generation(backup / "recovery-attestation.json", {"identity_generation_sha256": "foreign"})
    with pytest.raises(identity.Refused, match="recovery_generation_changed"):
        identity.restore_capture(backup, generation, None, None, None)


@pytest.mark.parametrize("full_gate_passes", [True, False])
def test_isolated_restore_preserves_original_gates_and_hides_private_proof(fresh_capture, monkeypatch, capsys, full_gate_passes):
    backup, generation, _, result = fresh_capture
    events = []
    operator = SimpleNamespace(records=lambda: events.append("current_inventory"))
    result["source_recovered"] = True
    monkeypatch.setattr(identity, "recovery_checks", lambda *_: (operator, result))
    identity.write_generation(backup / "recovery-attestation.json", {
        "identity_generation_sha256": identity.digest(identity.encoded(generation) + b"\n"),
    })
    paths = [backup / name for name in ("network.json", "host.json", "request.json")]
    for path in paths: identity.write_generation(path, {})
    restored = SimpleNamespace(
        Cluster=object,
        operator_lock=lambda: identity.locked(backup / "network.lock"),
        scope=SimpleNamespace(report_failure=lambda _: events.append("failure_evidence")),
    )
    monkeypatch.setitem(identity.sys.modules, "pg_index_guard", SimpleNamespace())
    monkeypatch.setattr(identity, "qualify_keycloak_profile", lambda *_: (backup / "profile.json", "digest"))
    monkeypatch.setattr(identity, "current_capture_cluster", lambda base, *_: base)
    def original_main():
        assert restored.cap is operator
        restored.cap.records()
        events.extend(["source_preserved", "fresh_owned_volumes", "network_isolation", "full_pg_restore", "logical_comparison"])
        print("SYNTHETIC PRIVATE SQL PROOF")
    restored.main = original_main
    gate = SimpleNamespace(require_metadata=object(), require_full_restore=lambda _: events.append("full_restore_gate") or full_gate_passes)
    monkeypatch.setattr(identity.importlib.util, "spec_from_file_location", lambda name, _: SimpleNamespace(name=name, loader=SimpleNamespace(exec_module=lambda _: None)))
    monkeypatch.setattr(identity.importlib.util, "module_from_spec", lambda spec: restored if spec.name == "vm_isolated_restore" else gate)
    argv = identity.sys.argv
    if full_gate_passes:
        identity.restore_capture(backup, generation, *paths)
    else:
        with pytest.raises(identity.Refused, match="full_restore_gate_failed"):
            identity.restore_capture(backup, generation, *paths)
    assert events == ["current_inventory", "source_preserved", "fresh_owned_volumes", "network_isolation", "full_pg_restore", "logical_comparison", "full_restore_gate"]
    assert "SYNTHETIC PRIVATE SQL PROOF" not in capsys.readouterr().out
    assert identity.sys.argv is argv


@pytest.fixture
def index_capture(private_directory, monkeypatch):
    operator = private_directory / "operator"
    operator.mkdir(mode=0o700)
    backup = private_directory / "capture"
    backup.mkdir(mode=0o700)
    (backup / "pg-keycloak").mkdir(mode=0o700)
    query = operator / "index_profile.sql"
    query.write_bytes(b"SELECT synthetic_index_profile")
    query.chmod(0o600)
    metadata = {"columns": [{"name": "client_id"}]}
    profile = {"format": "r10-keycloak-index-aliases-v1", "image": "pinned-image",
               "query_sha256": identity.digest(query.read_bytes()),
               "backup_manifest_sha256": "old-backup", "pg_manifest_sha256": "old-pg",
               "captured_metadata_sha256": identity.digest(identity.encoded(metadata))}
    identity.write_generation(operator / "qualified-index-profile.json", profile)
    monkeypatch.setattr(identity, "INDEX_PROFILE_SHA", identity.digest((operator / "qualified-index-profile.json").read_bytes()))
    monkeypatch.setattr(identity, "OPERATOR", operator)
    captured = {"image": "pinned-image", "database": "keycloak_test", "metadata": metadata}
    identity.write_generation(backup / "pg-keycloak/manifest.json", captured)
    manifest = {"images": {"keycloak-db": "pinned-image"}, "source_ids": {"keycloak-db": "current-source"},
                "archives": {"pg-keycloak/manifest.json": {"sha256": identity.digest((backup / "pg-keycloak/manifest.json").read_bytes())}}}
    identity.write_generation(backup / "manifest.json", manifest)
    source = {"Id": "current-source", "State": {"Running": True}}
    module = SimpleNamespace(records=lambda: {"keycloak-db": source}, PG={"keycloak-db": ("role", "keycloak_test", "5432")})
    cluster = SimpleNamespace(sql=lambda q: "150018" if q == "SHOW server_version_num" else "[]", metadata=lambda: metadata)
    comparisons = []
    def compare(expected, actual, old, indexes, version, collation):
        # Model a fail-closed sealed comparator; no live resources in unit tests.
        assert identity.digest(identity.encoded(expected)) == old["captured_metadata_sha256"]
        assert expected == actual and version == 150018
        comparisons.append((indexes, collation))
        return {"metadata_contract_equal": True, "index_semantics_equal": True, "raw_metadata_equal": True}
    guard = SimpleNamespace(compare=compare)
    return operator, backup, profile, manifest, captured, source, module, cluster, guard, comparisons


def test_current_capture_profile_requires_live_semantics_and_preserves_original(index_capture):
    operator, backup, _, _, _, _, module, cluster, guard, comparisons = index_capture
    before = (operator / "qualified-index-profile.json").read_bytes()
    path, checksum = identity.qualify_keycloak_profile(backup, {"generation": 1}, module, lambda *_: cluster, guard)
    fresh = json.loads(path.read_bytes())
    assert comparisons == [([], [])]
    assert fresh["backup_manifest_sha256"] == identity.digest((backup / "manifest.json").read_bytes())
    assert fresh["source_container_id"] == "current-source"
    assert fresh["identity_generation_sha256"] == identity.digest(identity.encoded({"generation": 1}) + b"\n")
    assert identity.digest(path.read_bytes()) == checksum
    assert (operator / "qualified-index-profile.json").read_bytes() == before
    # An existing witness cannot be rebound to another identity generation.
    with pytest.raises(identity.Refused, match="index_witness_conflict"):
        identity.qualify_keycloak_profile(backup, {"generation": 2}, module, lambda *_: cluster, guard)


@pytest.mark.parametrize("change", ["historical_profile", "query", "capture", "image", "database", "source", "stopped", "live_metadata", "semantics"])
def test_current_index_profile_refuses_drift_without_creating_witness(index_capture, change):
    operator, backup, _, manifest, captured, source, module, cluster, guard, _ = index_capture
    if change == "historical_profile": (operator / "qualified-index-profile.json").write_text("{}")
    elif change == "query": (operator / "index_profile.sql").write_text("changed query")
    elif change == "capture": (backup / "pg-keycloak/manifest.json").write_text("{}")
    elif change in {"image", "database"}:
        captured[change] = "unexpected"
        (backup / "pg-keycloak/manifest.json").write_bytes(identity.encoded(captured))
        manifest["archives"]["pg-keycloak/manifest.json"]["sha256"] = identity.digest((backup / "pg-keycloak/manifest.json").read_bytes())
        (backup / "manifest.json").write_bytes(identity.encoded(manifest))
    elif change == "source": source["Id"] = "foreign"
    elif change == "stopped": source["State"]["Running"] = False
    elif change == "live_metadata": cluster.metadata = lambda: {"columns": []}
    else: guard.compare = lambda *_: {"metadata_contract_equal": True, "index_semantics_equal": False}
    with pytest.raises((identity.Refused, AssertionError, KeyError)):
        identity.qualify_keycloak_profile(backup, {}, module, lambda *_: cluster, guard)
    assert not (backup / "qualified-keycloak-index-profile.json").exists()


def test_raw_metadata_path_still_uses_original_activation_gate():
    calls = []
    wrapped = identity.current_metadata_gate(lambda *args: calls.append(args) or "raw", None, None, None)
    assert wrapped("keycloak-db", {"metadata_equal": True}, {}, "backup", "pg", b"original") == "raw"
    assert len(calls) == 1


def test_cluster_validation_keeps_sealed_comparator_and_rejects_racing_metadata(private_directory):
    class Base:
        db = "keycloak_test"
        def metadata(self): return {"columns": ["actual"]}
        def validate_metadata(self, *_): return "original"
    calls = []
    hashing = lambda v: identity.digest(identity.encoded(v))
    proof = {"observed_metadata_sha256": hashing({"columns": ["actual"]})}
    guard = SimpleNamespace(digest=hashing, verify_cluster=lambda *args: calls.append(args) or proof)
    wrapped = identity.current_capture_cluster(Base, guard, private_directory, private_directory / "profile", "checksum")
    cluster = wrapped()
    actual, observed = cluster.validate_metadata({"expected": True}, private_directory / "pg-keycloak")
    assert observed is proof and calls[0][1] == {"expected": True}
    assert calls[0][-2:] == (private_directory / "manifest.json", private_directory / "pg-keycloak/manifest.json")
    proof["observed_metadata_sha256"] = "racing-change"
    with pytest.raises(identity.Refused, match="index_metadata_changed_during_comparison"):
        cluster.validate_metadata({}, private_directory / "pg-keycloak")
    cluster.db = "stockpilot_ci"
    assert cluster.validate_metadata({}, private_directory / "pg-application") == "original"


@pytest.mark.parametrize("change", [None, "image", "manifest", "pg", "profile", "witness", "result", "semantic_difference", "service"])
def test_current_activation_gate_binds_canonical_evidence(index_capture, change):
    operator, backup, _, _, _, _, module, cluster, guard, _ = index_capture
    path, checksum = identity.qualify_keycloak_profile(backup, {}, module, lambda *_: cluster, guard)
    profile = json.loads(path.read_bytes())
    hashing = lambda value: identity.digest(identity.encoded(value))
    witness = {"metadata": {"columns": ["qualified-alias"]}, "indexes": [], "version": 150018, "collation": []}
    compared = {"raw_metadata_equal": False, "metadata_contract_equal": True, "index_semantics_equal": True}
    result = {"metadata_equal": False, "metadata_contract_equal": True, "image": "pinned-image",
              "qualified_profile_sha256": checksum, "canonical_witness": witness,
              "canonical_witness_sha256": hashing(witness), "metadata_sha256": hashing(witness["metadata"]),
              "index_comparison": compared}
    capture = {"image": "pinned-image", "metadata": {}}
    guard.digest = hashing
    guard.compare = lambda *_: compared
    manifest_sha, pg_sha = profile["backup_manifest_sha256"], profile["pg_manifest_sha256"]
    service = "keycloak-db"
    if change == "image": capture["image"] = "foreign"
    elif change == "manifest": manifest_sha = "foreign"
    elif change == "pg": pg_sha = "foreign"
    elif change == "profile": path.write_text("{}")
    elif change == "witness": witness["version"] = 1
    elif change == "result": result["index_comparison"] = {}
    elif change == "service": service = "test-postgres"
    elif change == "semantic_difference":
        def refused(*_): raise RuntimeError("index_semantic_mismatch")
        guard.compare = refused
    wrapped = identity.current_metadata_gate(lambda *_: pytest.fail("no raw fallback"), guard, path, checksum)
    args = (service, result, capture, manifest_sha, pg_sha, (operator / "qualified-index-profile.json").read_bytes())
    if change is None: assert wrapped(*args) == "canonical-current-capture"
    else:
        with pytest.raises((identity.Refused, RuntimeError)): wrapped(*args)
