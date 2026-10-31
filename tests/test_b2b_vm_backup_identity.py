"""Offline synthetic identities and temporary files; no VM or Docker access."""

import copy
import json
import os
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
