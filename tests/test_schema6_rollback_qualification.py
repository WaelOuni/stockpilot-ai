"""Offline safety contracts for the local rollback build and disposable owner."""
import ast
import json
from pathlib import Path
import pytest
import yaml
from scripts.ops import schema6_rollback as build
from scripts.ci import qualify_schema6_rollback as lab

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", [
    "../secret", "/etc/passwd", "src/stockpilot/../../secret", "src\\stockpilot\\x",
    ".env", "src/stockpilot/.env.private", "src/stockpilot/__pycache__/secret",
    "backups/archive", "tests/secrets.py", ".git/config", "docker/compose.b2b-demo.yml",
])
def test_build_context_refuses_unapproved_or_unsafe_paths(path):
    assert not build.allowed_path(path)


def test_exact_approved_definitions_and_only_allowlist_change():
    current = (ROOT / build.RUNNER).read_bytes()
    original = current
    for name in build.ADDITIONS:
        original = original.replace(('        "' + name + '",\n').encode(), b"")
    build.allowlist_only(original, current)
    assert build.digest(original) == "e7feba6924844e736dcf8fdf37808954b5dd63d26135dabe4e71a3a2af8862d6"
    for path, checksum in build.HASHES.items():
        assert build.digest((ROOT / path).read_bytes()) == checksum
    for name in range(1, 7):
        assert len(list((ROOT / build.PREFIX / "migrations").glob(f"{name:04d}_*.json"))) == 1
    with pytest.raises(build.QualificationError, match="outside allowlist"):
        build.allowlist_only(original, current.replace(b'if len(rows) > len(self.migrations):',
                                                       b'if len(rows) > len(self.migrations) + 1:'))
    with pytest.raises(build.QualificationError, match="allowlist"):
        build.allowlist_only(original, current.replace(b'        "b2b_contact_replies",\n', b""))


def test_no_working_tree_files_enter_candidate(monkeypatch):
    original = {build.RUNNER: (ROOT / build.RUNNER).read_bytes()}
    for name in build.ADDITIONS:
        original[build.RUNNER] = original[build.RUNNER].replace(('        "' + name + '",\n').encode(), b"")
    original["src/stockpilot/business.py"] = b"immutable business"
    calls = []

    def git(repo, *args):
        calls.append(args)
        assert args[0] == "show" and args[1].startswith(build.APPROVED + ":")
        return (ROOT / args[1].split(":", 1)[1]).read_bytes()

    monkeypatch.setattr(build, "git", git)
    result = build.compatible_files(ROOT, original)
    assert result["src/stockpilot/business.py"] == b"immutable business"
    assert {p for p in result if result[p] != original.get(p)} == set(build.HASHES)
    assert len(calls) == 3


@pytest.mark.parametrize("mode,kind,path", [
    ("120000", "blob", "src/stockpilot/link"),
    ("160000", "commit", "src/stockpilot/submodule"),
    ("100644", "blob", "src/stockpilot/../private"),
])
def test_git_tree_rejects_links_submodules_and_traversal(monkeypatch, mode, kind, path):
    def fake(repo, *args):
        if args == ("rev-parse", "v0.2.3"):
            return build.TAG_OBJECT.encode()
        if args[0] == "cat-file" and args[1] == "-t":
            return b"tag"
        if args == ("rev-parse", "v0.2.3^{}"):
            return build.BASE.encode()
        if args[0] == "ls-tree":
            return f"{mode} {kind} {'1'*40}\t{path}\0".encode()
        raise AssertionError(args)
    monkeypatch.setattr(build, "git", fake)
    with pytest.raises(build.QualificationError, match="Unsafe"):
        build.source_files(ROOT)


@pytest.mark.parametrize("tag,peeled,kind", [
    ("0"*40, build.BASE, "tag"), (build.TAG_OBJECT, "0"*40, "tag"),
    (build.TAG_OBJECT, build.BASE, "commit"),
])
def test_moved_lightweight_or_wrong_tag_refused(monkeypatch, tag, peeled, kind):
    def fake(repo, *args):
        return (kind if args[0] == "cat-file" else tag if args[-1] == "v0.2.3" else peeled).encode()
    monkeypatch.setattr(build, "git", fake)
    with pytest.raises(build.QualificationError):
        build.source_files(ROOT)


@pytest.mark.parametrize("host", ["tcp://127.0.0.1:2375", "ssh://vm", "https://docker.example"])
def test_remote_daemon_refused(monkeypatch, host):
    monkeypatch.setenv("DOCKER_HOST", host)
    monkeypatch.setattr(lab, "command", lambda *a, **k: json.dumps([{
        "Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]).encode())
    with pytest.raises(build.QualificationError, match="Local Unix"):
        lab.local_engine()


def owned_record(tmp_path, owner):
    return {
        "Id": "cid", "Image": "sha256:image", "Config": {"Labels": {lab.LABEL: owner}},
        "HostConfig": {"PortBindings": {}, "PublishAllPorts": False, "Privileged": False, "NetworkMode": "nid"},
        "State": {"Status": "running"},
        "NetworkSettings": {"Networks": {"owned": {"NetworkID": "nid"}}},
        "Mounts": [{"Type": "tmpfs", "Destination": "/data/db"},
                   {"Type": "bind", "Source": str(tmp_path / "state")}],
    }


@pytest.mark.parametrize("corruption", ["label", "image", "port", "volume", "mount", "network", "privileged"])
def test_resource_attestation_refuses_foreign_or_exposed_targets(tmp_path, monkeypatch, corruption):
    owner = lab.Lab(tmp_path)
    owner.containers["cid"] = {"image": "sha256:image", "network": "nid", "service": "source"}
    record = owned_record(tmp_path, owner.owner)
    if corruption == "label":
        record["Config"]["Labels"][lab.LABEL] = "preexisting"
    elif corruption == "image":
        record["Image"] = "different"
    elif corruption == "port":
        record["HostConfig"]["PortBindings"] = {"27027/tcp": [{"HostIp": "127.0.0.1", "HostPort": "27027"}]}
    elif corruption == "volume":
        record["Mounts"][0] = {"Type": "volume", "Name": "production"}
    elif corruption == "mount":
        record["Mounts"][1]["Source"] = str(tmp_path.parent / "other")
    elif corruption == "network":
        record["NetworkSettings"]["Networks"]["other"] = {"NetworkID": "foreign"}
    else:
        record["HostConfig"]["Privileged"] = True
    monkeypatch.setattr(lab, "command", lambda *a, **k: json.dumps([record]).encode())
    with pytest.raises(build.QualificationError):
        owner.attest("cid")


def test_cleanup_only_acts_on_exact_owned_ids(tmp_path, monkeypatch):
    owner = lab.Lab(tmp_path)
    owner.containers["cid"] = {"image": "sha256:image", "network": "nid", "service": "source"}
    record = owned_record(tmp_path, owner.owner)
    commands = []

    def call(args, **kwargs):
        commands.append(args)
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([record]).encode()
        return b""
    monkeypatch.setattr(lab, "command", call)
    assert owner.cleanup() == {"removed_count": 1, "preexisting_resources_removed": 0}
    assert commands[-1] == ["docker", "rm", "-f", "cid"]


def test_required_ci_runs_actual_roundtrip_and_never_publishes():
    ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    job = ci["jobs"]["containers"]
    build_step = next(s for s in job["steps"] if "scripts/ops/schema6_rollback.py" in s.get("run", ""))
    run_step = next(s for s in job["steps"] if "scripts/ci/qualify_schema6_rollback.py" in s.get("run", ""))
    assert "if" not in build_step and "if" not in run_step and "if" not in job
    assert job["steps"].index(build_step) < job["steps"].index(run_step)
    assert "--build" in build_step["run"]
    assert "--current-image stockpilot-api:ci" in run_step["run"]
    assert "--keycloak-image stockpilot-keycloak:ci" in run_step["run"]
    assert ci["permissions"] == {"contents": "read"}
    assert ci["jobs"]["e2e"]["if"] == "$" + "{{ false }}"
    script = (ROOT / "scripts/ops/schema6_rollback.py").read_text() + (ROOT / "scripts/ci/qualify_schema6_rollback.py").read_text()
    assert '"push"' not in script and '"prune"' not in script
    assert "backup_fresh.py" not in script and "StockPilot-B2B-v021" not in script
    ast.parse(script)


def test_dump_inventory_is_dynamic_and_source_registry_not_mutated():
    source = (ROOT / "scripts/tests/schema6_rollback_probe.py").read_text()
    collector = (ROOT / "scripts/ops/recovery_contract.py").read_text()
    assert "return recovery_mongo_facts(db)" in source
    assert "for collection in db.list_collections()" in collector
    assert 'db.client.drop_database(name)' in source
    assert 'db.client.drop_database(db.name)' not in source
    assert 'case == "unknown-v7"' in source
    assert 'case == "validator"' in source
    assert 'MONITOR.enabled = True' in source

def test_remote_context_cannot_be_hidden_by_local_host_override(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    monkeypatch.setattr(lab, "command", lambda *a, **k: json.dumps([{
        "Endpoints": {"docker": {"Host": "ssh://protected"}}}]).encode())
    with pytest.raises(build.QualificationError, match="Local Unix"):
        lab.local_engine()


def test_logical_fingerprints_preserve_bson_numeric_types_and_field_order():
    from bson.int64 import Int64
    from scripts.tests.schema6_rollback_probe import canonical
    assert canonical({"value": 1}) != canonical({"value": Int64(1)})
    assert canonical({"value": 1}) != canonical({"value": 1.0})
    assert canonical({"key": [["org", 1], ["sku", 1]]}) != canonical({"key": [["sku", 1], ["org", 1]]})
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


@pytest.mark.parametrize("mode,kind,path", [
    ("120000", "blob", "frontend/src/link"),
    ("160000", "commit", "frontend/src/submodule"),
    ("100644", "blob", "frontend/src/../secret"),
    ("100644", "blob", "frontend/src/.env.private"),
    ("100644", "blob", "/etc/passwd"),
])
def test_historical_web_context_rejects_unsafe_entries(monkeypatch, mode, kind, path):
    from scripts.ci import build_schema6_browser as browser
    monkeypatch.setattr(browser, "source_files", lambda _: {})
    monkeypatch.setattr(browser, "git", lambda *args: f"{mode} {kind} {'1'*40}\t{path}\0".encode())
    with pytest.raises(build.QualificationError, match="Unsafe"):
        browser.web_files(ROOT)


def test_browser_is_mandatory_within_existing_container_job():
    ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    steps = ci["jobs"]["containers"]["steps"]
    browser = next(s for s in steps if "build_schema6_browser.py" in s.get("run", ""))
    roundtrip = next(s for s in steps if "qualify_schema6_rollback.py" in s.get("run", ""))
    assert "if" not in browser and steps.index(browser) < steps.index(roundtrip)
    assert '--browser-build-report "$RUNNER_TEMP/schema6-browser-build.json"' in roundtrip["run"]


def browser_capture():
    from scripts.tests.schema6_rollback_probe import BROWSER_IDENTITY_SCOPES
    return {"mongo": {"business": {"data": "exact-fields", "structure": "exact-structure"}}, "application": {"table": "exact-data"},
            "authorization_fences": {name: {
                "principal": {"value": 2, "present": True, "allowed": True},
                "other": {"value": 1, "present": True, "allowed": False}}
                for name in BROWSER_IDENTITY_SCOPES}}


def test_browser_read_contract_accepts_only_explicit_monotonic_authorization_fences():
    from scripts.tests.schema6_rollback_probe import compare_browser_business
    before, after = browser_capture(), browser_capture()
    for rows in after["authorization_fences"].values():
        rows["principal"]["value"] += 3
    assert set(compare_browser_business(before, after).values()) == {3}


@pytest.mark.parametrize("change", ["business", "postgres", "revoked", "removed", "decrement", "excess", "float"])
def test_browser_read_contract_never_hides_business_or_identity_changes(change):
    from scripts.tests.schema6_rollback_probe import compare_browser_business
    before, after = browser_capture(), browser_capture()
    for rows in after["authorization_fences"].values():
        rows["principal"]["value"] += 3
    row = after["authorization_fences"]["b2b_users"]["principal"]
    if change == "business":
        after["mongo"]["business"]["data"] = "role or reservation changed"
    elif change == "postgres":
        after["application"]["table"] = "conversation changed"
    elif change == "revoked":
        after["authorization_fences"]["b2b_users"]["other"]["value"] += 1
    elif change == "removed":
        row["present"] = False
    elif change == "decrement":
        row["value"] = 1
    elif change == "excess":
        row["value"] = 103
    else:
        row["value"] = 5.0
    with pytest.raises(AssertionError):
        compare_browser_business(before, after)
