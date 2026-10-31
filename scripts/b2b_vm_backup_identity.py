"""Reviewed adapter for the recovered VM's pinned backup operator.

Never installs itself. A generation is an integrity witness, not a backup or an
authorization. Local root and the Docker daemon remain trusted. R18's normal
attestation, including its transitive package seals, is mandatory; arbitrary
container recreation is not supported. No historical package is edited.
"""

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import http.client
import io
import json
import os
import re
import stat
import socket
import ssl
import subprocess
import sys
import traceback
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

PROJECT = "stockpilot-vm-recovered"
SOURCE = "b018bbf1c346bbcec11b4347e41175fad176f168"
SERVICES = {"bff", "proxy", "mongo", "test-postgres", "keycloak", "keycloak-db"}
ROOT = Path("/opt/stockpilot")
OPERATOR = ROOT / "operations/v13133-r13-e9f6297c41ba"
RECOVERY = ROOT / "operations/v13133-r18-c0858fa6496a/continuation_operator.py"
HISTORY = ROOT / "backups/v13133-before-upgrade-20261030T165610Z/manifest.json"
LOCK = Path("/run/lock/stockpilot-v13133-backup.lock")
INDEX_PROFILE_SHA = "676de98e1c8a8d7f29898e54975ec8487f70fcd83a5ccdbadc834444110445ab"
PINS = {
    OPERATOR / "backup_operator.py": "d48fa94d595b2b0a9741e52990a51058f0b6682b6b3d90cdba73b27701dd5f4e",
    OPERATOR / "package-manifest.json": "c755b7882f223170fd8ced43d5e4ef9fd1a82d67692094f130d1ef550fb7c191",
    RECOVERY: "f8114acb7420d4fb97c4d08bd85fb3df7cd3778b5a745a0ed81fed574a942b9d",
    HISTORY: "0eade0b86a48a84a8bfb49bfce162a7c4d505515bdde8fc9fd744e065d42a917",
}


class Refused(RuntimeError):
    """Only fixed, non-sensitive reason codes reach the console."""


def require(value, reason):
    if not value:
        raise Refused(reason)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def safe_parents(path):
    require(path.is_absolute(), "absolute_path_required")
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid(), "unsafe_parent")
        # /run/lock is commonly sticky; it is only used by locked(), not data I/O.
        require(not info.st_mode & 0o022, "writable_parent")


def private_bytes(path):
    safe_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "regular_file_required")
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o027, "private_file_required")
        require(info.st_size <= 16 * 1024 * 1024, "metadata_too_large")
        return stream.read()


@contextmanager
def locked(path=LOCK):
    require(path.parent.resolve() == path.parent, "lock_parent_link")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "r+") as handle:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_lock")
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o022, "unsafe_lock")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def verify_packages():
    for path, checksum in PINS.items():
        require(digest(private_bytes(path)) == checksum, "pinned_artifact_changed")
    package = json.loads(private_bytes(OPERATOR / "package-manifest.json"))
    for name, checksum in package["files"].items():
        require(Path(name).name == name, "package_path_escape")
        require(digest(private_bytes(OPERATOR / name)) == checksum, "operator_package_changed")


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=180, check=False)
    require(result.returncode == 0, "inspection_or_attestation_failed")
    return result.stdout


def normal_attestation():
    """Run unchanged R18 gates in a fresh interpreter under the caller's lock.

    R18's CLI takes the same lock itself. Calling its public gate sequence here
    avoids nesting flock; no gate is skipped, patched or reimplemented.
    """
    verify_packages()
    code = (
        "import importlib.util,json,sys; p=sys.argv[1]; "
        "sys.path.insert(0,str(__import__('pathlib').Path(p).parent)); "
        "s=importlib.util.spec_from_file_location('r18',p); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
        "c18,c16,a,old=m.package(); op=m.Continuation(c18,c16,a,old); "
        "op.initial_gate(); assert op.completed(); "
        "print(json.dumps(op.final_gate(repeated=True)))"
    )
    result = json.loads(command([sys.executable, "-B", "-c", code, str(RECOVERY)]))
    require(result.get("verified") is True and result.get("source") == SOURCE, "recovery_not_attested")
    require(result.get("renewal_enabled") is False, "unexpected_renewal")
    return result


def docker(*args):
    return command(["docker", "--host=unix:///var/run/docker.sock", *args])


def observe():
    """Hash sensitive configuration in memory; never serialize its plaintext."""
    ids = docker("ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT).split()
    require(len(ids) == 6, "service_set_changed")
    rows = json.loads(docker("inspect", *(x.decode() for x in ids)))
    records, containers, volumes, networks = {}, {}, {}, {}
    for row in rows:
        labels = row["Config"].get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        require(service in SERVICES and service not in records, "service_identity_changed")
        require(labels.get("com.docker.compose.project") == PROJECT, "project_changed")
        records[service] = row
        containers[service] = {
            "id": row["Id"], "image": row["Image"], "name": row["Name"],
            "config": digest(encoded(row["Config"])),
            "host": digest(encoded(row["HostConfig"])),
            "mounts": sorted(row["Mounts"], key=lambda m: m["Destination"]),
            "networks": {
                name: {k: value.get(k) for k in ("NetworkID", "IPAMConfig", "GwPriority")}
                for name, value in row["NetworkSettings"]["Networks"].items()
            },
        }
        for mount in row["Mounts"]:
            if mount["Type"] == "volume":
                name = mount["Name"]
                item = json.loads(docker("volume", "inspect", name))[0]
                volumes[name] = {k: item.get(k) for k in (
                    "Name", "Driver", "Mountpoint", "CreatedAt", "Scope", "Options", "Labels"
                )}
                mountpoint = Path(item["Mountpoint"])
                require(not any(p.is_symlink() for p in (mountpoint, *mountpoint.parents)), "volume_path_link")
                info = mountpoint.stat()
                volumes[name]["filesystem"] = [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
    for name in {n for c in containers.values() for n in c["networks"]}:
        item = json.loads(docker("network", "inspect", name))[0]
        require(set(item.get("Containers", {})) <= {r["Id"] for r in rows}, "foreign_network_consumer")
        networks[name] = {k: item.get(k) for k in (
            "Id", "Name", "Created", "Scope", "Driver", "EnableIPv6", "Internal",
            "Attachable", "Ingress", "IPAM", "Options", "Labels"
        )}
    files = {}
    for path in sorted((ROOT / "private").iterdir()):
        data = private_bytes(path)
        info = path.lstat()
        files[path.name] = {
            "sha256": digest(data), "bytes": len(data), "uid": info.st_uid,
            "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode),
            "device": info.st_dev, "inode": info.st_ino,
        }
    return records, {"containers": containers, "volumes": volumes, "networks": networks, "files": files}


def reconcile(history, snapshot, receipt):
    """Only the separately attested BFF ID may differ from the historical IDs."""
    require(history["source_project"] == PROJECT, "historical_project_changed")
    require(set(history["source_ids"]) == set(history["images"]) == SERVICES, "historical_services_changed")
    current = snapshot["containers"]
    require(set(current) == SERVICES, "service_set_changed")
    changes = {s for s in SERVICES if current[s]["id"] != history["source_ids"][s]}
    require(changes == {"bff"}, "unexpected_identity_transition")
    require(receipt.get("verified") is True and receipt.get("source") == SOURCE, "recovery_not_attested")
    require(receipt.get("renewal_enabled") is False, "unexpected_renewal")
    require(current["bff"]["id"] == receipt.get("bff_id"), "bff_not_attested")
    require(current["proxy"]["id"] == receipt.get("proxy_id"), "proxy_not_attested")
    for service, item in current.items():
        require(item["image"] == history["images"][service], "historical_image_changed")
        actual = {m["Name"] for m in item["mounts"] if m["Type"] == "volume"}
        require(actual == set(history["source_volumes"][service]), "historical_volumes_changed")
    return {
        "format": 1, "created_utc": datetime.now(UTC).isoformat(), "source": SOURCE,
        "project": PROJECT, "pins": {str(p): h for p, h in PINS.items()},
        "attestation": copy.deepcopy(receipt), "snapshot": copy.deepcopy(snapshot),
    }


def write_generation(path, value):
    safe_parents(path)
    data = encoded(value) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    require(private_bytes(path) == data, "generation_write_verification_failed")
    return digest(data)


def load_generation(path, checksum):
    require(bool(re.fullmatch(r"[a-f0-9]{64}", checksum or "")), "generation_digest_required")
    raw = private_bytes(path)
    require(stat.S_IMODE(path.lstat().st_mode) == 0o600, "generation_permissions_changed")
    require(digest(raw) == checksum, "generation_digest_changed")
    value = json.loads(raw)
    require(value.get("format") == 1 and value.get("source") == SOURCE and value.get("project") == PROJECT, "generation_scope_changed")
    require(value.get("pins") == {str(p): h for p, h in PINS.items()}, "generation_pins_changed")
    return value


def guarded_records(generation, history, observer=observe, attester=normal_attestation):
    first = True

    def records():
        nonlocal first
        if first:
            # capture's original main() has acquired its normal flock by now.
            receipt = attester()
        rows, snapshot = observer()
        require(snapshot == generation["snapshot"], "runtime_generation_changed")
        if first:
            reconcile(history, snapshot, receipt)
            first = False
        return rows

    return records


def load_operator():
    verify_packages()
    path = OPERATOR / "backup_operator.py"
    sys.path.insert(0, str(OPERATOR))
    spec = importlib.util.spec_from_file_location("vm_legacy_backup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke_operator(mode, generation, history):
    module = load_operator()
    # Replace the obsolete ID-only inventory reader, not preflight/capture/OCI,
    # database, restore or recovery gates. EXPECTED and on-disk code stay intact.
    require(module.EXPECTED == history["source_ids"], "legacy_identity_contract_changed")
    module.records = guarded_records(generation, history)
    previous = sys.argv
    try:
        sys.argv = [str(OPERATOR / "backup_operator.py"), "preflight" if mode == "preflight" else "capture-authorized-v13133"]
        module.main()
    finally:
        sys.argv = previous


def capture_metadata(backup, generation):
    """Admit only a fresh capture of this generation, never a historical backup."""
    require(backup.parent == ROOT / "backups", "capture_location_refused")
    require(bool(re.fullmatch(r"v13133-before-upgrade-[0-9]{8}T[0-9]{6}Z", backup.name)), "capture_name_refused")
    manifest = json.loads(private_bytes(backup / "manifest.json"))
    result = json.loads(private_bytes(backup / "operator-result.json"))
    created = datetime.fromisoformat(generation["created_utc"])
    captured = datetime.fromisoformat(manifest["captured_at_utc"])
    require(created.tzinfo is not None and captured.tzinfo is not None, "capture_time_missing")
    require(created <= captured <= datetime.now(UTC), "capture_predates_generation")
    require(manifest["format"] == 2 and manifest["postgres_backup_format"] == "pg-full-fidelity-v1", "capture_format_refused")
    require(manifest["source_project"] == PROJECT, "capture_project_changed")
    containers = generation["snapshot"]["containers"]
    require(manifest["source_ids"] == {s: c["id"] for s, c in containers.items()}, "capture_ids_changed")
    require(manifest["images"] == {s: c["image"] for s, c in containers.items()}, "capture_images_changed")
    require(set(manifest["source_volumes"]) == SERVICES, "capture_volumes_changed")
    for service, container in containers.items():
        names = [m["Name"] for m in container["mounts"] if m["Type"] == "volume"]
        require(sorted(manifest["source_volumes"][service]) == sorted(names), "capture_volumes_changed")
    require(manifest["private_file_hashes"] == {n: f["sha256"] for n, f in generation["snapshot"]["files"].items()}, "capture_configuration_changed")
    require(result.get("capture_valid") is True and result.get("containers_restarted") is True, "capture_incomplete")
    require(result.get("backup") == str(backup), "capture_result_location_changed")
    return manifest, result


def post_capture_operator(backup, generation):
    """R18 start-time witnesses cannot survive the authorized capture restart.

    Its replacement gate is the fresh capture's exact generation binding and
    immutable resource comparison, plus the original health/facts/HTTPS gates.
    This gate cannot admit an old capture or a changed container ID.
    """
    capture_metadata(backup, generation)
    module = load_operator()

    def records():
        rows, snapshot = observe()
        require(snapshot == generation["snapshot"], "runtime_generation_changed")
        return rows

    module.records = records
    return module


def archive_digest(path):
    safe_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "archive_file_refused")
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o027, "archive_permissions_refused")
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
        return value.hexdigest(), info.st_size


def verify_archives(backup, manifest):
    expected = {"mongo.archive.gz", "private-state.tar", "runtime-state.tar", "application-images.tar", "image-archive-proof.json", "image-runtime-proof.json"}
    expected |= {f"pg-{db}/{name}" for db in ("application", "keycloak") for name in ("globals.sql", "database.dump", "manifest.json")}
    require(set(manifest["archives"]) == expected, "capture_archive_set_changed")
    for name, item in manifest["archives"].items():
        checksum, size = archive_digest(backup / name)
        require(checksum == item["sha256"] and size == item["bytes"], "capture_archive_changed")


def https_ready(config):
    context = ssl.create_default_context(cafile=str(ROOT / "private/ca.crt"))
    for url in (config["BFF_ORIGIN"] + "/b2b-app/", config["BFF_ISSUER"] + "/.well-known/openid-configuration"):
        target = urlsplit(url)
        require(target.scheme == "https" and target.hostname in {"b2b-vm.stockpilot.test", "auth-vm.stockpilot.test"}, "https_target_refused")
        require(target.port in {61914, 61915}, "https_port_refused")
        connection = http.client.HTTPSConnection(target.hostname, target.port, context=context, timeout=10)
        try:
            connection.sock = context.wrap_socket(socket.create_connection(("192.168.250.2", target.port), timeout=10), server_hostname=target.hostname)
            connection.request("GET", target.path)
            response = connection.getresponse()
            body = response.read()
            require(response.status == 200, "https_readiness_failed")
            if "well-known" in target.path:
                require(json.loads(body)["issuer"] == config["BFF_ISSUER"], "issuer_changed")
        finally:
            connection.close()


def recovery_checks(backup, generation):
    manifest, result = capture_metadata(backup, generation)
    module = post_capture_operator(backup, generation)
    require(docker("info", "--format", "{{.ID}}").decode().strip() == manifest["source_engine_id"], "capture_engine_changed")
    rows = module.records()
    require(all(r["State"]["Running"] for r in rows.values()), "source_not_running")
    for service in ("bff", "proxy", "keycloak"):
        module.healthy(rows[service]["Id"])
    config = module.config()
    module.facts(rows, config)
    https_ready(config)
    verify_archives(backup, manifest)
    module.records()
    return module, result


def verify_recovery(backup, generation):
    """Called under the backup lock. Only this fresh capture's status is updated."""
    _, result = recovery_checks(backup, generation)
    proof = {"source_recovered": True, "https_tls_oidc_verified": True,
             "same_ids_images_private_files": True,
             "identity_generation_sha256": digest(encoded(generation) + b"\n")}
    path = backup / "recovery-attestation.json"
    if path.exists():
        require(json.loads(private_bytes(path)) == proof, "recovery_proof_conflict")
    else:
        write_generation(path, proof)
    result.update(source_recovered=True, external_validation_pending=False)
    # Preserve the original result; a failed write can never mark it recovered.
    before = private_bytes(backup / "operator-result.json")
    preserved = backup / "operator-result.before-identity-recovery.json"
    if not preserved.exists():
        write_generation(preserved, json.loads(before))
    pending = backup / "operator-result.identity-recovery.json"
    write_generation(pending, result)
    require(private_bytes(backup / "operator-result.json") == before, "capture_result_changed")
    os.replace(pending, backup / "operator-result.json")
    parent = os.open(backup, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    require(json.loads(private_bytes(backup / "operator-result.json")) == result, "recovery_result_write_failed")
    print(json.dumps({"source_recovered": True, "backup": str(backup)}))


def qualify_keycloak_profile(backup, generation, module, cluster_type, guard):
    """Rebind an unchanged, live-qualified index contract to this fresh capture.

    The old profile is never edited. Its metadata, image, collation, server and
    four index semantics must still match the new capture AND the live source.
    Only the two capture provenance hashes change in a new private witness.
    """
    raw = private_bytes(OPERATOR / "qualified-index-profile.json")
    require(digest(raw) == INDEX_PROFILE_SHA, "index_profile_changed")
    profile = json.loads(raw)
    require(profile["format"] == "r10-keycloak-index-aliases-v1", "index_profile_format")
    manifest_bytes = private_bytes(backup / "manifest.json")
    manifest = json.loads(manifest_bytes)
    pg_bytes = private_bytes(backup / "pg-keycloak/manifest.json")
    captured = json.loads(pg_bytes)
    require(digest(pg_bytes) == manifest["archives"]["pg-keycloak/manifest.json"]["sha256"], "pg_capture_changed")
    require(captured["image"] == profile["image"] == manifest["images"]["keycloak-db"], "index_image_changed")
    require(captured["database"] == "keycloak_test", "index_database_changed")
    query = private_bytes(OPERATOR / "index_profile.sql")
    require(digest(query) == profile["query_sha256"], "index_query_changed")
    source = module.records()["keycloak-db"]
    require(source["Id"] == manifest["source_ids"]["keycloak-db"] and source["State"]["Running"], "index_source_changed")
    cluster = cluster_type(source["Id"], *module.PG["keycloak-db"])

    def rows(sql):
        return json.loads(cluster.sql("SELECT coalesce(json_agg(t),'[]'::json) FROM (" + sql + ") t"))

    comparison = guard.compare(
        captured["metadata"], cluster.metadata(), profile, rows(query.decode()),
        int(cluster.sql("SHOW server_version_num")),
        rows("SELECT datcollate,datctype,datlocprovider,daticulocale,datcollversion FROM pg_database WHERE datname=current_database()"),
    )
    require(comparison["metadata_contract_equal"] is True and comparison["index_semantics_equal"] is True, "index_source_not_qualified")
    module.records()
    require(private_bytes(backup / "manifest.json") == manifest_bytes and private_bytes(backup / "pg-keycloak/manifest.json") == pg_bytes, "index_capture_changed")
    fresh = copy.deepcopy(profile)
    fresh.update(
        backup_manifest_sha256=digest(manifest_bytes), pg_manifest_sha256=digest(pg_bytes),
        predecessor_profile_sha256=digest(raw),
        identity_generation_sha256=digest(encoded(generation) + b"\n"),
        source_container_id=source["Id"], source_comparison=comparison,
    )
    path = backup / "qualified-keycloak-index-profile.json"
    if path.exists():
        require(json.loads(private_bytes(path)) == fresh, "index_witness_conflict")
    else:
        write_generation(path, fresh)
    return path, digest(private_bytes(path))


def current_capture_cluster(base, guard, backup, profile_path, profile_sha):
    class CaptureCluster(base):
        def validate_metadata(self, expected, folder):
            if self.db != "keycloak_test":
                return super().validate_metadata(expected, folder)
            require(Path(folder) == backup / "pg-keycloak", "index_restore_folder_changed")
            actual = self.metadata()
            proof = guard.verify_cluster(self, expected, profile_path, profile_sha,
                                         backup / "manifest.json", Path(folder) / "manifest.json")
            require(guard.digest(actual) == proof["observed_metadata_sha256"], "index_metadata_changed_during_comparison")
            return actual, proof

        def restore(self, folder, expected_image, owner):
            result = super().restore(folder, expected_image, owner)
            if self.db == "keycloak_test" and result["metadata_equal"] is False:
                profile = json.loads(private_bytes(profile_path))
                require(digest(private_bytes(profile_path)) == profile_sha, "index_witness_changed")
                query = private_bytes(OPERATOR / "index_profile.sql")
                require(digest(query) == profile["query_sha256"], "index_query_changed")

                def rows(sql):
                    return json.loads(self.sql("SELECT coalesce(json_agg(t),'[]'::json) FROM (" + sql + ") t"))

                witness = {"metadata": self.metadata(), "indexes": rows(query.decode()),
                           "version": int(self.sql("SHOW server_version_num")),
                           "collation": rows("SELECT datcollate,datctype,datlocprovider,daticulocale,datcollversion FROM pg_database WHERE datname=current_database()")}
                capture = json.loads(private_bytes(Path(folder) / "manifest.json"))
                compared = guard.compare(capture["metadata"], witness["metadata"], profile,
                                         witness["indexes"], witness["version"], witness["collation"])
                require(compared == result["index_comparison"] and guard.digest(witness["metadata"]) == result["metadata_sha256"], "index_restore_witness_changed")
                result.update(canonical_witness=witness, canonical_witness_sha256=guard.digest(witness),
                              qualified_profile_sha256=profile_sha)
            return result
    return CaptureCluster


def current_metadata_gate(original, guard, profile_path, profile_sha):
    def verify(service, result, capture, manifest_sha, pg_sha, historical_bytes):
        if result["metadata_equal"] is True:
            return original(service, result, capture, manifest_sha, pg_sha, historical_bytes)
        require(service == "keycloak-db" and result["metadata_equal"] is False, "unqualified_metadata_difference")
        require(digest(historical_bytes) == INDEX_PROFILE_SHA, "historical_index_profile_changed")
        raw = private_bytes(profile_path)
        require(digest(raw) == profile_sha == result["qualified_profile_sha256"], "current_index_profile_changed")
        profile = json.loads(raw)
        require(profile["backup_manifest_sha256"] == manifest_sha and profile["pg_manifest_sha256"] == pg_sha, "current_index_provenance_changed")
        require(profile["image"] == capture["image"] == result["image"], "current_index_image_changed")
        witness = result["canonical_witness"]
        require(guard.digest(witness) == result["canonical_witness_sha256"], "current_index_witness_changed")
        require(guard.digest(witness["metadata"]) == result["metadata_sha256"], "current_metadata_witness_changed")
        compared = guard.compare(capture["metadata"], witness["metadata"], profile,
                                 witness["indexes"], witness["version"], witness["collation"])
        require(compared["raw_metadata_equal"] is False and result["metadata_contract_equal"] is True
                and result["index_comparison"] == compared, "current_index_result_changed")
        return "canonical-current-capture"
    return verify


def restore_capture(backup, generation, network_plan, host_inventory, request):
    """Use the original isolated restore/ownership/PG gates, with current IDs."""
    module, result = recovery_checks(backup, generation)
    require(result.get("source_recovered") is True, "source_recovery_not_attested")
    proof = json.loads(private_bytes(backup / "recovery-attestation.json"))
    require(proof.get("identity_generation_sha256") == digest(encoded(generation) + b"\n"), "recovery_generation_changed")
    for path in (network_plan, host_inventory, request):
        require(path is not None and path.parent == backup, "restore_input_location_refused")
        private_bytes(path)
    previous = sys.argv
    try:
        sys.argv = [str(OPERATOR / "restore_operator.py"), "restore-authorized-v13133", str(backup), str(network_plan), str(host_inventory), str(request)]
        spec = importlib.util.spec_from_file_location("vm_isolated_restore", OPERATOR / "restore_operator.py")
        restored = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(restored)
        restored.cap = module
        import pg_index_guard
        profile, checksum = qualify_keycloak_profile(backup, generation, module, restored.Cluster, pg_index_guard)
        restored.Cluster = current_capture_cluster(restored.Cluster, pg_index_guard, backup, profile, checksum)
        with restored.operator_lock():
            try:
                # Full proof stays in the original private destination; do not
                # stream SQL metadata and account evidence to the console.
                with redirect_stdout(io.StringIO()):
                    restored.main()
            except Exception as error:
                # Exception strings and subprocess bodies may contain secrets.
                restored.scope.report_failure({"restore_verified": False, "adapter_failure": True,
                    "error_type": type(error).__name__,
                    "stack": [(frame.name, frame.lineno) for frame in traceback.extract_tb(error.__traceback__)]})
                raise
        gate_spec = importlib.util.spec_from_file_location("vm_restore_gate", OPERATOR / "activation_gate.py")
        gate = importlib.util.module_from_spec(gate_spec)
        gate_spec.loader.exec_module(gate)
        gate.require_metadata = current_metadata_gate(gate.require_metadata, pg_index_guard, profile, checksum)
        require(gate.require_full_restore(backup) is True, "full_restore_gate_failed")
        print(json.dumps({"restore_verified": True, "backup": str(backup)}))
    finally:
        sys.argv = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "preflight", "capture-authorized", "verify-capture", "restore-authorized"])
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--sha256")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--network-plan", type=Path)
    parser.add_argument("--host-inventory", type=Path)
    parser.add_argument("--restore-request", type=Path)
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0, "root_required")
        require(not sys.flags.optimize and not os.environ.get("PYTHONOPTIMIZE"), "optimized_assertions_refused")
        require(not any(k.startswith("DOCKER_") for k in os.environ), "docker_override_refused")
        require(command(["docker", "context", "show"]).strip() == b"default", "docker_context_refused")
        require(args.generation.parent == ROOT / "backups", "generation_location_refused")
        require(bool(re.fullmatch(r"vm-backup-identity-[a-zA-Z0-9-]+\.json", args.generation.name)), "generation_name_refused")
        verify_packages()
        history = json.loads(private_bytes(HISTORY))
        if args.mode == "plan":
            with locked():
                receipt = normal_attestation()
                _, before = observe()
                value = reconcile(history, before, receipt)
                _, after = observe()
                require(before == after, "runtime_changed_during_attestation")
                checksum = write_generation(args.generation, value)
            print(json.dumps({"generation": str(args.generation), "sha256": checksum, "backup_created": False}))
        else:
            generation = load_generation(args.generation, args.sha256)
            if args.mode in {"verify-capture", "restore-authorized"}:
                require(args.backup is not None, "capture_required")
                with locked():
                    if args.mode == "verify-capture":
                        verify_recovery(args.backup, generation)
                    else:
                        restore_capture(args.backup, generation, args.network_plan, args.host_inventory, args.restore_request)
            elif args.mode == "preflight":
                with locked():
                    invoke_operator(args.mode, generation, history)
            else:
                # The pinned original capture main owns this same lock before
                # calling guarded_records and keeps it through source recovery.
                invoke_operator(args.mode, generation, history)
        return 0
    except (Exception, SystemExit):
        print("NO-GO: backup identity operation refused; no automatic repair.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
