"""Physical-only restore primitives. No database engine, repair or cleanup.

Callers must first run the cold safety preflight and anchor archive digests.
Only fresh, attested local volumes can receive a validated tar stream.
"""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from datetime import datetime, timezone

EXTRACT_ARGUMENTS = ["--extract", "--file=-", "--directory=/restore", "--numeric-owner",
                     "--same-owner", "--same-permissions", "--delay-directory-restore"]


class RestoreRefused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RestoreRefused(reason)


def inventory(stream, *, max_members=10000, max_bytes=1024**3):
    """Read every payload; reject links, ambiguous paths and unqualified metadata."""
    result = {}
    total = 0
    with tarfile.open(fileobj=stream, mode="r:") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            name = str(path)
            require(not path.is_absolute() and ".." not in path.parts
                    and not any(ord(c) < 32 or ord(c) == 127 for c in member.name), "unsafe_tar_path")
            require(name not in result and len(result) < max_members, "duplicate_path_or_member_limit")
            require(member.isfile() or member.isdir(), "link_or_special_member")
            require(not member.sparse and set(member.pax_headers) <= {
                "path", "size", "uid", "gid", "uname", "gname", "mtime"
            }, "unqualified_extended_metadata")
            require(type(member.uid) is int and type(member.gid) is int
                    and 0 <= member.uid <= 2147483647 and 0 <= member.gid <= 2147483647
                    and 0 <= member.mode <= 0o7777 and member.mtime >= 0, "invalid_numeric_metadata")
            require(member.size >= 0 and (member.isfile() or member.size == 0), "invalid_member_size")
            total += member.size
            require(total <= max_bytes, "payload_limit")
            row = {"type": "file" if member.isfile() else "directory", "bytes": member.size,
                   "mode": member.mode, "uid": member.uid, "gid": member.gid, "mtime": member.mtime}
            if member.isfile():
                with archive.extractfile(member) as data:
                    row["sha256"] = hashlib.file_digest(data, "sha256").hexdigest()
            result[name] = row
    require(result.get(".", {}).get("type") == "directory", "tar_root_required")
    for name in result:
        for parent in PurePosixPath(name).parents:
            require(result.get(str(parent), {}).get("type") == "directory", "missing_or_non_directory_parent")
    return result


def checked_archive(path, expected_sha256):
    require(re.fullmatch(r"[a-f0-9]{64}", expected_sha256 or ""), "archive_digest_required")
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "regular_archive_required")
    with path.open("rb") as stream:
        require(hashlib.file_digest(stream, "sha256").hexdigest() == expected_sha256, "archive_digest_mismatch")
        stream.seek(0)
        return inventory(stream)


def execute(argv, *, incoming=None, timeout=300):
    try:
        result = subprocess.run(argv, stdin=incoming, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise RestoreRefused("command_unavailable_or_timeout") from None
    require(result.returncode == 0, "command_nonzero_exit")
    require(not result.stderr, "unexpected_stderr")
    return result.stdout


class RestoreSession:
    """A campaign ledger limits all writes to newly created volumes and helpers."""

    def __init__(self, prefix, image, evidence, preexisting_volumes):
        require(re.fullmatch(r"stockpilot-v1394-r1-[a-f0-9]{32}", prefix or ""), "invalid_campaign_prefix")
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", image or ""), "immutable_image_required")
        self.prefix, self.image = prefix, image
        self.evidence = Path(evidence)
        require(self.evidence.is_dir() and self.evidence.stat().st_mode & 0o777 == 0o700
                and self.evidence.stat().st_uid == os.getuid()
                and all(not p.is_symlink() for p in (self.evidence, *self.evidence.parents))
                and "b2b-backups" not in self.evidence.parts, "private_evidence_required")
        require(not (self.evidence / (prefix + "-resources.json")).exists(), "campaign_ledger_already_exists")
        self.preexisting = dict(preexisting_volumes)
        self.volumes, self.helpers = {}, {}
        info = self.data(["docker", "image", "inspect", image])[0]
        require(info["Id"] == image and not info["Config"].get("Volumes"), "helper_image_has_implicit_volumes")

    def data(self, args):
        try:
            return json.loads(execute(args))
        except (ValueError, UnicodeError):
            raise RestoreRefused("invalid_docker_json") from None

    def record(self):
        # A private ledger is written immediately after every creation, never in the bundle.
        path = self.evidence / (self.prefix + "-resources.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"prefix": self.prefix, "image": self.image,
                       "volumes": self.volumes, "helpers": self.helpers}, stream, indent=2)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())

    def event(self, phase, **fields):
        path = self.evidence / (self.prefix + "-events.jsonl")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps({"date": datetime.now(timezone.utc).isoformat(), "phase": phase, **fields}) + "\n")
            stream.flush(); os.fsync(stream.fileno())

    def new_volume(self, suffix):
        require(re.fullmatch(r"[a-z][a-z0-9-]{0,45}", suffix or ""), "invalid_resource_suffix")
        name = self.prefix + "-" + suffix
        known = execute(["docker", "volume", "ls", "-q"]).decode().splitlines()
        require(name not in known and name not in self.preexisting and name not in self.volumes, "volume_already_exists")
        execute(["docker", "volume", "create", "--driver", "local",
                 "--label", "stockpilot.audit.purpose=v1394-r1-physical-restore",
                 "--label", "stockpilot.restore.campaign=" + self.prefix, name])
        self.volumes[name] = None
        self.record()
        self.attest_volume(name, first=True)
        return name

    def attest_volume(self, name, *, first=False):
        require(name in self.volumes and name not in self.preexisting, "volume_not_created_by_campaign")
        row = self.data(["docker", "volume", "inspect", name])[0]
        labels = row.get("Labels") or {}
        require(row["Name"] == name and row["Driver"] == "local" and not row.get("Options")
                and row["Scope"] == "local"
                and labels.get("stockpilot.restore.campaign") == self.prefix
                and labels.get("stockpilot.audit.purpose") == "v1394-r1-physical-restore", "volume_identity_or_driver_mismatch")
        mountpoint = row["Mountpoint"]
        forbidden = {v["Mountpoint"] for v in self.preexisting.values()}
        other = {v["Mountpoint"] for n, v in self.volumes.items() if v and n != name}
        require(mountpoint == "/var/lib/docker/volumes/" + name + "/_data"
                and mountpoint not in forbidden | other, "shared_or_unexpected_mountpoint")
        if not first:
            require(row == self.volumes[name], "volume_changed_since_creation")
        self.volumes[name] = row
        self.record()
        self.event("volume_attested", name=name, mountpoint=mountpoint)
        return row

    def new_helper(self, suffix, volume=None, *, user="0:0", arguments=None):
        name = self.prefix + "-" + suffix
        require(re.fullmatch(r"[a-z][a-z0-9-]{0,45}", suffix or ""), "invalid_resource_suffix")
        require(user in ("0:0", "999:999"), "unapproved_helper_user")
        names = execute(["docker", "ps", "-a", "--format", "{{.Names}}"]).decode().splitlines()
        require(name not in names, "container_already_exists")
        if volume is not None:
            self.attest_volume(volume)
        arguments = list(arguments or ["--version"])
        require(arguments == ["--version"] or (arguments == EXTRACT_ARGUMENTS and user == "0:0"), "unapproved_tar_arguments")
        args = ["docker", "container", "create", "--name", name, "--pull=never", "--network", "none",
                "--restart=no", "--no-healthcheck", "--read-only", "--cap-drop=ALL", "--user", user,
                "--cap-add=CHOWN", "--cap-add=DAC_OVERRIDE", "--cap-add=FOWNER", "--cap-add=FSETID",
                "--env", "TAR_OPTIONS=",
                "--memory", "512m", "--cpus", "1", "--pids-limit", "32", "--interactive",
                "--label", "stockpilot.audit.purpose=v1394-r1-physical-restore",
                "--label", "stockpilot.restore.campaign=" + self.prefix]
        if volume is not None:
            args += ["--mount", "type=volume,source=" + volume + ",target=/restore,volume-nocopy"]
        cid = execute([*args, "--entrypoint", "/bin/tar", self.image, *arguments]).decode().strip()
        require(re.fullmatch(r"[a-f0-9]{64}", cid), "invalid_created_container_id")
        self.helpers[cid] = {"name": name, "volume": volume, "user": user, "arguments": arguments}
        self.record()
        self.attest_helper(cid, stopped=True)
        return cid

    def attest_helper(self, cid, *, stopped=False):
        require(cid in self.helpers, "helper_not_created_by_campaign")
        expected = self.helpers[cid]
        row = self.data(["docker", "inspect", cid])[0]
        config, host = row["Config"], row["HostConfig"]
        environment = dict(value.split("=", 1) for value in config.get("Env", []) if "=" in value)
        require(environment.get("TAR_OPTIONS") == "", "inherited_tar_options_refused")
        require(row["Id"] == cid and row["Name"] == "/" + expected["name"] and row["Image"] == self.image
                and config["Entrypoint"] == ["/bin/tar"] and config["Cmd"] == expected["arguments"]
                and config["User"] == expected["user"] and config["Healthcheck"]["Test"] == ["NONE"]
                and (config.get("Labels") or {}).get("stockpilot.restore.campaign") == self.prefix, "helper_identity_or_entrypoint_mismatch")
        require(host["NetworkMode"] == "none" and not host.get("PortBindings") and host["ReadonlyRootfs"]
                and host["RestartPolicy"]["Name"] == "no" and not host.get("Privileged")
                and not host.get("PidMode") and host.get("IpcMode") == "private" and not host.get("UTSMode")
                and host["CapDrop"] == ["ALL"] and {c.removeprefix("CAP_") for c in host.get("CapAdd") or []} == {"CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID"}
                and not host.get("Binds") and not host.get("VolumesFrom"), "unsafe_helper_configuration")
        mounts = row["Mounts"]
        if expected["volume"] is None:
            require(not mounts, "unexpected_helper_mount")
        else:
            self.attest_volume(expected["volume"])
            require(len(mounts) == 1 and mounts[0]["Type"] == "volume"
                    and mounts[0]["Name"] == expected["volume"] and mounts[0]["Destination"] == "/restore"
                    and mounts[0]["RW"] is True, "unexpected_helper_mount")
            users = execute(["docker", "ps", "-aq", "--no-trunc", "--filter", "volume=" + expected["volume"]]).decode().split()
            require(users == [cid], "unexpected_volume_consumer")
        if stopped:
            require(row["State"]["Status"] == "created" and not row["State"]["Running"]
                    and row["State"]["Pid"] == 0, "helper_already_started")
        self.event("helper_attested", container=cid, stopped=stopped)
        return row

    def readback(self, cid):
        self.attest_helper(cid)
        require(self.helpers[cid]["volume"] is not None, "helper_without_volume")
        raw = execute(["docker", "cp", cid + ":/restore/.", "-"], timeout=300)
        require(len(raw) <= 1024**3, "readback_size_limit")
        return inventory(io.BytesIO(raw))

    def start_helper(self, cid, *, incoming=None):
        self.attest_helper(cid, stopped=True)
        self.event("helper_starting", container=cid, volume=self.helpers[cid]["volume"], arguments=self.helpers[cid]["arguments"])
        result = execute(["docker", "start", "--attach", "--interactive", cid], incoming=incoming)
        row = self.attest_helper(cid)
        require(row["State"]["Status"] == "exited" and row["State"]["ExitCode"] == 0
                and not row["State"]["Running"] and not row["State"]["OOMKilled"]
                and row["RestartCount"] == 0, "helper_did_not_exit_successfully")
        return result

    def restore(self, suffix, archive, sha256):
        # No destination or extraction may be created before full archive validation.
        expected = checked_archive(archive, sha256)
        volume = self.new_volume(suffix)
        cid = self.new_helper("extract-" + suffix, volume, arguments=EXTRACT_ARGUMENTS)
        before = self.readback(cid)
        require(set(before) == {"."}, "destination_not_empty")
        self.event("destination_empty", container=cid, volume=volume)
        with Path(archive).open("rb") as stream:
            # Re-anchor the same open file immediately before sending its bytes.
            require(hashlib.file_digest(stream, "sha256").hexdigest() == sha256, "archive_changed_before_import")
            stream.seek(0)
            self.start_helper(cid, incoming=stream)
        actual = self.readback(cid)
        require(actual == expected, "restored_file_or_metadata_mismatch")
        self.event("fidelity_verified", container=cid, volume=volume, members=len(actual),
                   inventory_sha256=hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest())
        return {"container": cid, "volume": volume, "members": len(actual),
                "files": sum(x["type"] == "file" for x in actual.values()),
                "directories": sum(x["type"] == "directory" for x in actual.values()),
                "archive_sha256": sha256, "root": actual["."], "identical": True}
