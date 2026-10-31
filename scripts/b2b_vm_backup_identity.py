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
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

PROJECT = "stockpilot-vm-recovered"
SOURCE = "b018bbf1c346bbcec11b4347e41175fad176f168"
SERVICES = {"bff", "proxy", "mongo", "test-postgres", "keycloak", "keycloak-db"}
ROOT = Path("/opt/stockpilot")
OPERATOR = ROOT / "operations/v13133-r13-e9f6297c41ba"
RECOVERY = ROOT / "operations/v13133-r18-c0858fa6496a/continuation_operator.py"
HISTORY = ROOT / "backups/v13133-before-upgrade-20261030T165610Z/manifest.json"
LOCK = Path("/run/lock/stockpilot-v13133-backup.lock")
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


def invoke_operator(mode, generation, history):
    verify_packages()
    path = OPERATOR / "backup_operator.py"
    sys.path.insert(0, str(OPERATOR))
    spec = importlib.util.spec_from_file_location("vm_legacy_backup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Replace the obsolete ID-only inventory reader, not preflight/capture/OCI,
    # database, restore or recovery gates. EXPECTED and on-disk code stay intact.
    require(module.EXPECTED == history["source_ids"], "legacy_identity_contract_changed")
    module.records = guarded_records(generation, history)
    previous = sys.argv
    try:
        sys.argv = [str(path), "preflight" if mode == "preflight" else "capture-authorized-v13133"]
        module.main()
    finally:
        sys.argv = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "preflight", "capture-authorized"])
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--sha256")
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
            if args.mode == "preflight":
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
