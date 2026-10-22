"""Prepare private upgrade artifacts; never apply them or mutate Docker resources.

stage is offline. plan reads the local Docker engine and refuses unexpected
identities. Both write only new files inside an existing private backup point.
Neither command authorizes an intervention or proves database restorability.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SOURCE_SHA = "cb0c7eb37642bdcf2cc16a11edfdad3636c4d017"
TARGET_SHA = "d36d6586c962ebcba2658b814b2692cfd919211e"
SERVICES = {"bff", "proxy", "mongo", "test-postgres", "keycloak", "keycloak-db"}
RUNTIME = {"bff", "proxy"}
GROUP_FILES = {
    "bff.json",
    "operator.json",
    "tls.crt",
    "tls.key",
    "ca.crt",
    "realm.json",
    "mongo.key",
    "proxy.conf",
}


def require(condition):
    if not condition:
        raise ValueError("Private preparation refused.")


def image_id(value):
    require(isinstance(value, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", value))
    return value


def expected_compose(source, api, web, *, publish):
    result = copy.deepcopy(source)
    for name, iid in (("bff", api), ("operator", api), ("proxy", web)):
        result["services"][name]["image"] = image_id(iid)
    for kind in ("volumes", "networks"):
        for name, definition in result[kind].items():
            result[kind][name] = {"name": definition["name"], "external": True}
    for service in result["services"].values():
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                require(mount.get("read_only") is True)
                mount.setdefault("bind", {})["create_host_path"] = False
    if not publish:
        result["services"]["proxy"]["ports"] = []
    return result


def mounts(items):
    return sorted(
        (m["Type"], m["Destination"], m.get("Name", ""), m.get("Source", ""), m["RW"])
        for m in items
    )


def port_bindings(service):
    result = {}
    for port in service.get("ports", []):
        require(port["host_ip"] == "127.0.0.1")
        result[str(port["target"]) + "/" + port.get("protocol", "tcp")] = [
            {"HostIp": port["host_ip"], "HostPort": str(port["published"])}
        ]
    return result


def planned_manifest(
    source,
    current,
    composition,
    inventory,
    rows,
    volumes,
    networks,
    *,
    api,
    web,
    revision,
    compose_hash,
):
    """Pure validation of already observed facts. No file or Docker I/O."""
    require(source["revision"] == SOURCE_SHA)
    require(revision in {SOURCE_SHA, TARGET_SHA})
    require(source["project"] == "stockpilot-b2b-local")
    require(source["purpose"] == "v139-local-synthetic" and source["phase"] == "ready")
    require(source.keys() == current.keys())
    require(set(source["containers"]) == set(source["images"]) == SERVICES)
    require(set(current["containers"]) == set(current["images"]) == SERVICES)
    mutable = {"containers", "images", "revision", "built_images", "files"}
    require(all(source[k] == current[k] for k in source.keys() - mutable))
    require(source["files"].keys() == current["files"].keys())
    require(
        all(
            source["files"][k] == current["files"][k]
            for k in source["files"]
            if k != "compose.json"
        )
    )
    require(set(inventory) == SERVICES and len(rows) == len(SERVICES))
    records = {}
    for row in rows:
        labels = row["Config"].get("Labels") or {}
        name = labels.get("com.docker.compose.service")
        require(name in SERVICES and name not in records)
        require(labels.get("com.docker.compose.project") == source["project"])
        require(labels.get("stockpilot.audit.purpose") == source["purpose"])
        require(
            labels.get("com.docker.compose.project.config_files")
            == source["state"] + "/compose.json"
        )
        require(inventory[name]["id"] == source["containers"][name])
        require(inventory[name]["image"] == source["images"][name])
        require(mounts(row["Mounts"]) == mounts(inventory[name]["mounts"]))
        require(
            set(row["NetworkSettings"]["Networks"]) == set(inventory[name]["networks"])
        )
        require(
            all(
                v["NetworkID"] == source["networks"][n]
                for n, v in row["NetworkSettings"]["Networks"].items()
            )
        )
        require(
            (row["HostConfig"].get("PortBindings") or {})
            == port_bindings(composition["services"][name])
        )
        require(not row["State"].get("OOMKilled", False))
        if name not in RUNTIME:
            require(
                row["Id"] == source["containers"][name] == current["containers"][name]
            )
            require(row["Image"] == source["images"][name] == current["images"][name])
        else:
            require(row["Image"] == {"bff": api, "proxy": web}[name])
        if name != "keycloak" or composition["services"]["proxy"].get("ports"):
            require(row["State"]["Running"])
            require(
                row["State"].get("Health", {}).get("Status", "healthy") == "healthy"
            )
        elif row["State"]["Running"]:
            require(
                row["State"].get("Health", {}).get("Status", "healthy") == "healthy"
            )
        records[name] = row
    require(set(volumes) == set(source["volumes"]))
    require(volumes == source["volumes"])
    require(set(networks) == set(source["networks"]))
    ids = {r["Id"] for r in records.values()}
    for name, network in networks.items():
        require(network["Id"] == source["networks"][name])
        labels = network.get("Labels") or {}
        require(labels.get("com.docker.compose.project") == source["project"])
        require(labels.get("stockpilot.audit.purpose") == source["purpose"])
        require(labels.get("stockpilot.installation") == source["installation"])
        require(not (set(network.get("Containers", {})) - ids))
    result = copy.deepcopy(current)
    for name in RUNTIME:
        result["containers"][name] = records[name]["Id"]
        result["images"][name] = records[name]["Image"]
    result["built_images"] = {api: api, web: web}
    result["revision"] = revision
    result["files"]["compose.json"] = compose_hash
    return result


def private_bytes(path, mode=0o600):
    require(not path.is_symlink() and path.is_file())
    st = path.stat()
    require(st.st_uid == os.getuid() and st.st_mode & 0o777 == mode)
    require(st.st_size <= 4 * 1024 * 1024)
    return path.read_bytes()


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "plan"))
    parser.add_argument("--point-dir", required=True, type=Path)
    parser.add_argument("--direction", choices=("upgrade", "rollback"), required=True)
    parser.add_argument("--ports", choices=("closed", "open"), default="closed")
    args = parser.parse_args()
    try:
        from scripts.b2b_interactive import (
            DEFAULT_STATE,
            TEMPLATE,
            InteractiveDemo,
            command,
            volume_identity,
        )

        point = args.point_dir.absolute()
        require(point.parent == DEFAULT_STATE.parent / "b2b-backups")
        require(point.name.startswith("v1393-point-"))
        require(all(not p.is_symlink() for p in (point, *point.parents)))
        require(point.is_dir() and point.stat().st_uid == os.getuid())
        require(point.stat().st_mode & 0o777 == 0o700)
        source = json.loads(private_bytes(point / "source-manifest.json"))
        baseline = json.loads(private_bytes(point / "source-compose.json"))
        require(source["state"] == str(DEFAULT_STATE))
        require(source["revision"] == SOURCE_SHA and source["version"] == 1)
        require(source["project"] == baseline["name"] == "stockpilot-b2b-local")
        require(
            hashlib.sha256(private_bytes(point / "source-compose.json")).hexdigest()
            == source["files"]["compose.json"]
        )
        if args.direction == "upgrade":
            api = image_id(private_bytes(point / "api.iid").decode().strip())
            web = image_id(private_bytes(point / "web.iid").decode().strip())
            revision = TARGET_SHA
        else:
            api, web = source["images"]["bff"], source["images"]["proxy"]
            revision = SOURCE_SHA
        candidate = expected_compose(baseline, api, web, publish=args.ports == "open")
        prefix = args.direction + "-" + args.ports
        if args.action == "stage":
            write_new(point / (prefix + "-compose.json"), candidate)
        else:
            # A maintenance shell must hold the operator lock throughout cutover.
            # This command never takes over a lock or writes the canonical state.
            d = InteractiveDemo()
            require(
                DEFAULT_STATE.is_dir() and DEFAULT_STATE.stat().st_uid == os.getuid()
            )
            require(DEFAULT_STATE.stat().st_mode & 0o777 == 0o700)
            require(
                all(not p.is_symlink() for p in (DEFAULT_STATE, *DEFAULT_STATE.parents))
            )
            current = json.loads(private_bytes(DEFAULT_STATE / "manifest.json"))
            actual_bytes = private_bytes(DEFAULT_STATE / "compose.json")
            require(json.loads(actual_bytes) == candidate)
            require(
                source["template_sha256"]
                == hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
            )
            require(
                {p.name for p in DEFAULT_STATE.iterdir()}
                == {*source["files"], "manifest.json"}
            )
            for name, checksum in source["files"].items():
                require(Path(name).name == name)
                if name != "compose.json":
                    mode = 0o640 if name in GROUP_FILES else 0o600
                    require(
                        hashlib.sha256(
                            private_bytes(DEFAULT_STATE / name, mode)
                        ).hexdigest()
                        == checksum
                    )
            inventory = json.loads(private_bytes(point / "source-inventory.json"))
            d.docker()
            ids = command(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    "label=com.docker.compose.project=" + source["project"],
                ]
            ).split()
            require(bool(ids))
            rows = json.loads(command(["docker", "inspect", *ids]))
            volumes = {
                n: volume_identity(
                    json.loads(command(["docker", "volume", "inspect", n]))[0]
                )
                for n in source["volumes"]
            }
            networks = {
                n: json.loads(command(["docker", "network", "inspect", n]))[0]
                for n in source["networks"]
            }
            if args.direction == "upgrade":
                for iid in {api, web}:
                    image = json.loads(command(["docker", "image", "inspect", iid]))[0]
                    require(image["Id"] == iid)
                    require(
                        image["Config"]
                        .get("Labels", {})
                        .get("org.opencontainers.image.revision")
                        == TARGET_SHA
                    )
            result = planned_manifest(
                source,
                current,
                candidate,
                inventory,
                rows,
                volumes,
                networks,
                api=api,
                web=web,
                revision=revision,
                compose_hash=hashlib.sha256(actual_bytes).hexdigest(),
            )
            write_new(point / (prefix + "-before-manifest.json"), current)
            write_new(point / (prefix + "-manifest.json"), result)
        print(
            "Private candidate prepared. No canonical file or Docker resource changed."
        )
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        IndexError,
        AttributeError,
    ):
        print(
            "NO-GO: private preparation refused; details withheld. No automatic repair."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
