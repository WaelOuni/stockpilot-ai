"""Offline failure cases for paired-recovery contracts; no real credentials/DB."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tarfile

from bson.int64 import Int64
import pytest

from scripts.ops import recovery_contract as c
from scripts.ops.prepare_recovery_patch import PINS, prepare, replace_once


def capture():
    mongo = {name: {"count": 1, "data": "a" * 64, "options": {}, "indexes": []}
             for name in c.NEW_COLLECTIONS | c.EPHEMERAL | {c.HISTORY}}
    mongo[c.HISTORY]["count"] = 6
    pg = {"public.fixture": {"count": 1, "data": "b" * 64, "structure": "c" * 64},
          "_roles": {"ci": [True, True, True, True, True, False, -1, True, None]},
          "_sequences": "d" * 64, "_memberships": [], "_extensions": [["plpgsql", "1.0", "pg_catalog"]]}
    return {"mongo": mongo, "application": pg, "keycloak": copy.deepcopy(pg),
            "schema": {"version": 6, "checksums": list(c.CHECKSUMS)},
            "excluded_ephemeral": []}


@pytest.fixture
def paired(tmp_path):
    active, backup = tmp_path / "active", tmp_path / "backup"
    active.mkdir(mode=0o700); backup.mkdir(mode=0o700)
    images = {name: "local/qualification-" + name + "@sha256:" + "e" * 64
              for name in ("api", "web", "mongo", "postgres", "keycloak-db", "keycloak")}
    for relative in c.PROVENANCE_PATHS:
        path = active / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(images) if relative == "inputs/images.json" else "synthetic fixture\n")
    for name in c.ARCHIVES - {"configuration.tar.gz", "facts.json"}:
        (backup / name).write_bytes(b"synthetic archive fixture")
    (backup / "facts.json").write_text(json.dumps(capture()))
    with tarfile.open(backup / "configuration.tar.gz", "w:gz") as archive:
        for name in c.PROVENANCE_PATHS:
            archive.add(active / name, arcname=name)
    manifest = {"format": c.FORMAT, "installation": "synthetic-installation",
                "provenance": c.source_provenance(active), "images": images, "schema": capture()["schema"]}
    def seal():
        manifest["archives"] = {n: {"bytes": (backup / n).stat().st_size,
                                     "sha256": c.file_sha(backup / n)} for n in c.ARCHIVES}
        (backup / "manifest.json").write_text(json.dumps(manifest))
        return c.file_sha(backup / "manifest.json")
    seal()
    return active, backup, manifest, seal


def verify(paired, digest=None):
    active, backup, _, _ = paired
    return c.verify_backup(backup, digest or c.file_sha(backup / "manifest.json"),
                           installation="synthetic-installation", active_root=active)


def test_manifest_accepts_only_complete_paired_provenance(paired):
    assert verify(paired)["format"] == c.FORMAT


@pytest.mark.parametrize("name", sorted(c.ARCHIVES))
def test_each_archive_corruption_is_refused(paired, name):
    _, backup, _, _ = paired
    (backup / name).write_bytes((backup / name).read_bytes() + b"corrupt")
    with pytest.raises(c.ContractError):
        verify(paired)


def test_manifest_digest_is_independent_not_self_asserted(paired):
    _, backup, _, _ = paired
    digest = c.file_sha(backup / "manifest.json")
    (backup / "manifest.json").write_text("{}")
    with pytest.raises(c.ContractError):
        verify(paired, digest)


@pytest.mark.parametrize("path", c.PROVENANCE_PATHS)
def test_active_operator_or_configuration_drift_is_refused(paired, path):
    active, _, _, _ = paired
    (active / path).write_text("different synthetic configuration")
    with pytest.raises(c.ContractError):
        verify(paired)


@pytest.mark.parametrize("change", ["legacy", "installation", "missing_archive", "traversal", "unpinned"])
def test_malformed_manifest_fails_closed_even_with_approved_digest(paired, change):
    _, backup, manifest, _ = paired
    if change == "legacy":
        manifest["format"] = "stockpilot-v021-fresh-paired-v1"
    elif change == "installation":
        manifest["installation"] = "different"
    elif change == "missing_archive":
        manifest["archives"].pop("facts.json")
    elif change == "traversal":
        manifest["archives"]["../outside.dump"] = manifest["archives"].pop("application.dump")
    else:
        manifest["images"]["api"] = "local/qualification-api:latest"
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(c.ContractError):
        verify(paired)


@pytest.mark.parametrize("location", ["archive", "operator", "backup_root", "parent"])
def test_symlinks_are_refused(paired, tmp_path, location):
    active, backup, _, _ = paired
    if location in {"archive", "operator"}:
        path = backup / "application.dump" if location == "archive" else active / "operations/backup_fresh.py"
        original = tmp_path / "original"
        path.rename(original);path.symlink_to(original)
        with pytest.raises(c.ContractError):
            verify(paired)
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(backup if location == "backup_root" else tmp_path, target_is_directory=True)
        root = alias if location == "backup_root" else alias / "backup"
        with pytest.raises(c.ContractError):
            c.checked_file(root, "manifest.json")


@pytest.mark.parametrize("kind", ["data", "count", "options", "indexes"])
def test_mongo_drift_is_never_normalized(kind):
    before, after = capture(), capture()
    row = after["mongo"]["b2b_contact_replies"]
    row[kind] = {"lost": True} if kind == "options" else [{"key": ["changed"]}] if kind == "indexes" else 2 if kind == "count" else "f" * 64
    with pytest.raises(c.ContractError):
        c.compare_capture(before, after)


@pytest.mark.parametrize("kind", ["data", "structure", "_sequences", "_roles", "_memberships", "_extensions"])
def test_postgres_business_structure_grants_and_state_drift_refused(kind):
    before, after = capture(), capture()
    if kind.startswith("_"):
        after["application"][kind] = {"unexpected": True}
    else:
        after["application"]["public.fixture"][kind] = "f" * 64
    with pytest.raises(c.ContractError):
        c.compare_capture(before, after)


@pytest.mark.parametrize("scope,name", [("mongo", "b2b_order_archives"), ("mongo", c.HISTORY),
                                       ("application", "_sequences"), ("keycloak", "public.fixture")])
def test_missing_inventory_or_metadata_refused(scope, name):
    before, after = capture(), capture()
    del after[scope][name]
    with pytest.raises(c.ContractError):
        c.compare_capture(before, after)


def test_ephemeral_exception_preserves_structure_and_never_covers_business():
    before, after = capture(), capture()
    before["excluded_ephemeral"] = after["excluded_ephemeral"] = ["bff_sessions"]
    after["mongo"]["bff_sessions"]["data"] = "f" * 64
    after["mongo"]["bff_sessions"]["count"] = 0
    assert c.compare_capture(before, after)
    after["mongo"]["bff_sessions"]["options"] = {"validator": {}}
    with pytest.raises(c.ContractError):
        c.compare_capture(before, after)
    before["excluded_ephemeral"] = after["excluded_ephemeral"] = ["b2b_orders"]
    with pytest.raises(c.ContractError):
        c.compare_capture(before, after)


def test_only_explicit_additional_restore_role_is_normalized():
    before, after = capture(), capture()
    for scope in ("application", "keycloak"):
        after[scope]["_roles"]["restore_admin"] = c.BOOTSTRAP_ROLE_ATTRIBUTES.copy()
    assert c.compare_capture(before, after, restore_role="restore_admin")
    after["application"]["_roles"]["ci"][0] = False
    with pytest.raises(c.ContractError):
        c.compare_capture(before, after, restore_role="restore_admin")


def test_bootstrap_role_already_in_source_is_ambiguous():
    before, after = capture(), capture()
    for d in (before, after):
        d["application"]["_roles"]["restore_admin"] = c.BOOTSTRAP_ROLE_ATTRIBUTES.copy()
    with pytest.raises(c.ContractError):
        c.compare_capture(before, after, restore_role="restore_admin")


def test_bson_keeps_numeric_types():
    assert len({c.canonical(1), c.canonical(Int64(1)), c.canonical(1.0)}) == 3


def test_migration_checksum_constants_equal_immutable_registry():
    from stockpilot.infrastructure.persistence.mongodb.schema_migrations import registry
    assert tuple(m.checksum for m in registry()) == c.CHECKSUMS


def test_lock_refuses_concurrent_holder_and_never_steals(tmp_path):
    lock = tmp_path / "recovery.lock"
    first = c.operation_lock(lock)
    try:
        with pytest.raises(BlockingIOError):
            c.operation_lock(lock)
        assert lock.is_file()
    finally:
        os.close(first)
    second = c.operation_lock(lock)
    os.close(second)
    assert lock.is_file()


def test_lock_refuses_symlink_and_unsafe_permissions(tmp_path):
    target = tmp_path / "target";target.touch(mode=0o600)
    alias = tmp_path / "alias";alias.symlink_to(target)
    with pytest.raises(OSError):
        c.operation_lock(alias)
    target.chmod(0o644)
    with pytest.raises(c.ContractError):
        c.operation_lock(target)


def test_patch_refuses_unpinned_private_source_without_output(tmp_path, monkeypatch):
    for name in PINS:
        (tmp_path / name).write_text("synthetic unknown revision")
    monkeypatch.setattr("scripts.ops.prepare_recovery_patch.tempfile.mkdtemp",
                        lambda **_: pytest.fail("output created before source verification"))
    with pytest.raises(c.ContractError):
        prepare(tmp_path)


@pytest.mark.parametrize("source", ["missing", "anchor anchor"])
def test_patch_requires_exact_single_anchor(source):
    with pytest.raises(c.ContractError):
        replace_once(source, "anchor", "replacement")

def test_dynamic_mongo_inventory_preserves_unknown_collection_and_options():
    class Collection:
        def find(self, query):
            return [{"value": Int64(1)}]
        def list_indexes(self):
            return [{"name": "compound", "key": {"tenant": 1, "sku": -1}, "unique": True}]
    class Database:
        def list_collections(self):
            return [{"name": "future_fixture", "options": {"validator": {"value": {"$type": "long"}}}}]
        def __getitem__(self, name):
            assert name == "future_fixture"
            return Collection()
    proof = c.mongo_facts(Database())
    assert set(proof) == {"future_fixture"}
    assert proof["future_fixture"]["count"] == 1
    assert proof["future_fixture"]["options"] == {"validator": {"value": {"$type": "long"}}}
    assert proof["future_fixture"]["indexes"][0]["key"] == [
        ["tenant", {"$numberInt": "1"}], ["sku", {"$numberInt": "-1"}]]


def test_manifest_schema_must_match_captured_schema(paired):
    _, backup, manifest, _ = paired
    manifest["schema"] = {"version": 4, "checksums": list(c.CHECKSUMS[:4])}
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(c.ContractError, match="captured schema"):
        verify(paired)


def test_lock_refuses_symlink_parent_before_creating_a_file(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(c.ContractError, match="parent"):
        c.operation_lock(alias / "recovery.lock")
    assert not (real / "recovery.lock").exists()
