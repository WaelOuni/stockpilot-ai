"""Simulated inventory only: no Docker calls and no persistent installation I/O."""

import copy
import hashlib
import json

import pytest

from scripts.b2b_upgrade_manifest import (
    SOURCE_SHA,
    TARGET_SHA,
    expected_compose,
    planned_manifest,
    write_new,
)


@pytest.fixture
def facts():
    names = ("bff", "proxy", "mongo", "test-postgres", "keycloak", "keycloak-db")
    state = "/private/b2b-demo"
    images = {n: "sha256:" + str(i) * 64 for i, n in enumerate(names, 1)}
    network_name = "stockpilot-b2b-local_application"
    volumes = {
        "stockpilot-b2b-local_data": {
            "Name": "stockpilot-b2b-local_data",
            "CreatedAt": "original",
        }
    }
    source = {
        "revision": SOURCE_SHA,
        "state": state,
        "project": "stockpilot-b2b-local",
        "purpose": "v139-local-synthetic",
        "phase": "ready",
        "installation": "a" * 32,
        "containers": {n: n + "-original" for n in names},
        "images": images,
        "volumes": volumes,
        "networks": {network_name: "network-original"},
        "built_images": {"original-api": images["bff"]},
        "files": {"compose.json": "old-compose", "bff.json": "original-secret-hash"},
    }
    baseline = {
        "name": source["project"],
        "volumes": {
            "data": {
                "name": "stockpilot-b2b-local_data",
                "labels": {"original": "label"},
            }
        },
        "networks": {"application": {"name": network_name, "internal": True}},
        "services": {n: {"image": images[n]} for n in names},
    }
    baseline["services"]["operator"] = {"image": images["bff"]}
    baseline["services"]["bff"]["volumes"] = [
        {
            "type": "bind",
            "source": state + "/bff.json",
            "target": "/run/bff.json",
            "read_only": True,
            "bind": {"create_host_path": True},
        }
    ]
    baseline["services"]["proxy"]["ports"] = [
        {
            "target": 57914,
            "published": "57914",
            "host_ip": "127.0.0.1",
            "protocol": "tcp",
        }
    ]
    api, web = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    candidate = expected_compose(baseline, api, web, publish=False)
    rows, inventory = [], {}
    for n in names:
        mount_list = []
        if n == "bff":
            mount_list = [
                {
                    "Type": "bind",
                    "Source": state + "/bff.json",
                    "Destination": "/run/bff.json",
                    "RW": False,
                }
            ]
        if n == "mongo":
            mount_list = [
                {
                    "Type": "volume",
                    "Name": "stockpilot-b2b-local_data",
                    "Destination": "/data/db",
                    "Source": "/docker/data",
                    "RW": True,
                }
            ]
        inventory[n] = {
            "id": source["containers"][n],
            "image": images[n],
            "mounts": copy.deepcopy(mount_list),
            "networks": [network_name],
        }
        rows.append(
            {
                "Id": n + "-new" if n in {"bff", "proxy"} else source["containers"][n],
                "Image": {"bff": api, "proxy": web}.get(n, images[n]),
                "Config": {
                    "Labels": {
                        "com.docker.compose.service": n,
                        "com.docker.compose.project": source["project"],
                        "stockpilot.audit.purpose": source["purpose"],
                        "com.docker.compose.project.config_files": state
                        + "/compose.json",
                    }
                },
                "Mounts": mount_list,
                "HostConfig": {"PortBindings": {}},
                "NetworkSettings": {
                    "Networks": {network_name: {"NetworkID": "network-original"}}
                },
                "State": {"Running": n != "keycloak", "Health": {"Status": "healthy"}},
            }
        )
    networks = {
        network_name: {
            "Id": "network-original",
            "Labels": {
                "com.docker.compose.project": source["project"],
                "stockpilot.audit.purpose": source["purpose"],
                "stockpilot.installation": source["installation"],
            },
            "Containers": {r["Id"]: {} for r in rows},
        }
    }
    return {
        "source": source,
        "current": copy.deepcopy(source),
        "composition": candidate,
        "inventory": inventory,
        "rows": rows,
        "volumes": copy.deepcopy(volumes),
        "networks": networks,
        "api": api,
        "web": web,
        "revision": TARGET_SHA,
        "compose_hash": "new-compose",
    }, baseline


def test_only_runtime_identities_revision_builds_and_compose_hash_change(facts):
    kwargs, _ = facts
    before = copy.deepcopy(kwargs)
    result = planned_manifest(**kwargs)
    assert kwargs == before
    assert result["revision"] == TARGET_SHA
    assert result["containers"]["bff"] == "bff-new"
    assert result["containers"]["proxy"] == "proxy-new"
    for key in ("volumes", "networks", "installation", "state", "purpose"):
        assert result[key] == kwargs["source"][key]
    assert result["files"]["bff.json"] == "original-secret-hash"
    assert result["files"]["compose.json"] == "new-compose"


@pytest.mark.parametrize(
    "attack",
    [
        "foreign_project",
        "wrong_file",
        "duplicate_service",
        "missing_service",
        "infra_id",
        "infra_image",
        "writable_bind",
        "new_mount",
        "volume_replaced",
        "volume_extra",
        "network_replaced",
        "foreign_endpoint",
        "changed_network_attachment",
        "host_port",
        "runtime_image",
        "changed_secret",
        "changed_installation",
        "unhealthy",
        "oom",
        "stopped_db",
    ],
)
def test_refuses_identity_data_and_isolation_changes(facts, attack):
    kwargs, _ = facts
    bff = next(
        r
        for r in kwargs["rows"]
        if r["Config"]["Labels"]["com.docker.compose.service"] == "bff"
    )
    mongo = next(
        r
        for r in kwargs["rows"]
        if r["Config"]["Labels"]["com.docker.compose.service"] == "mongo"
    )
    network = next(iter(kwargs["networks"].values()))
    if attack == "foreign_project":
        bff["Config"]["Labels"]["com.docker.compose.project"] = "other"
    elif attack == "wrong_file":
        bff["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/other"
    elif attack == "duplicate_service":
        kwargs["rows"][-1] = copy.deepcopy(bff)
    elif attack == "missing_service":
        kwargs["rows"].pop()
    elif attack == "infra_id":
        mongo["Id"] = "recreated-mongo"
    elif attack == "infra_image":
        mongo["Image"] = kwargs["api"]
    elif attack == "writable_bind":
        bff["Mounts"][0]["RW"] = True
    elif attack == "new_mount":
        bff["Mounts"].append(copy.deepcopy(mongo["Mounts"][0]))
    elif attack == "volume_replaced":
        next(iter(kwargs["volumes"].values()))["CreatedAt"] = "new"
    elif attack == "volume_extra":
        kwargs["volumes"]["other"] = {}
    elif attack == "network_replaced":
        network["Id"] = "other"
    elif attack == "foreign_endpoint":
        network["Containers"]["foreign"] = {}
    elif attack == "changed_network_attachment":
        next(iter(bff["NetworkSettings"]["Networks"].values()))["NetworkID"] = "foreign"
    elif attack == "host_port":
        bff["HostConfig"]["PortBindings"] = {
            "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]
        }
    elif attack == "runtime_image":
        bff["Image"] = kwargs["source"]["images"]["bff"]
    elif attack == "changed_secret":
        kwargs["current"]["files"]["bff.json"] = "changed"
    elif attack == "changed_installation":
        kwargs["current"]["installation"] = "other"
    elif attack == "unhealthy":
        bff["State"]["Health"]["Status"] = "unhealthy"
    elif attack == "oom":
        bff["State"]["OOMKilled"] = True
    elif attack == "stopped_db":
        mongo["State"]["Running"] = False
    with pytest.raises(ValueError):
        planned_manifest(**kwargs)


def test_rollback_uses_observed_new_container_ids_and_original_image_ids(facts):
    kwargs, baseline = facts
    kwargs["current"] = planned_manifest(**kwargs)
    kwargs["api"] = kwargs["source"]["images"]["bff"]
    kwargs["web"] = kwargs["source"]["images"]["proxy"]
    kwargs["revision"] = SOURCE_SHA
    kwargs["composition"] = expected_compose(
        baseline, kwargs["api"], kwargs["web"], publish=False
    )
    for row in kwargs["rows"]:
        name = row["Config"]["Labels"]["com.docker.compose.service"]
        if name in {"bff", "proxy"}:
            row["Id"] = name + "-rollback"
            row["Image"] = kwargs["source"]["images"][name]
    network = next(iter(kwargs["networks"].values()))
    network["Containers"] = {r["Id"]: {} for r in kwargs["rows"]}
    result = planned_manifest(**kwargs)
    assert result["revision"] == SOURCE_SHA
    assert result["containers"]["bff"] == "bff-rollback"
    assert result["images"] == kwargs["source"]["images"]


def test_stage_keeps_source_unchanged_and_refuses_resource_creation(facts):
    kwargs, baseline = facts
    original = copy.deepcopy(baseline)
    staged = expected_compose(baseline, kwargs["api"], kwargs["web"], publish=True)
    assert baseline == original
    assert (
        staged["services"]["proxy"]["ports"] == original["services"]["proxy"]["ports"]
    )
    assert all(
        d["external"] for kind in ("volumes", "networks") for d in staged[kind].values()
    )
    assert not staged["services"]["bff"]["volumes"][0]["bind"]["create_host_path"]


def test_open_admission_requires_keycloak_running_and_healthy(facts):
    kwargs, baseline = facts
    kwargs["composition"] = expected_compose(
        baseline, kwargs["api"], kwargs["web"], publish=True
    )
    proxy = next(
        r
        for r in kwargs["rows"]
        if r["Config"]["Labels"]["com.docker.compose.service"] == "proxy"
    )
    keycloak = next(
        r
        for r in kwargs["rows"]
        if r["Config"]["Labels"]["com.docker.compose.service"] == "keycloak"
    )
    proxy["HostConfig"]["PortBindings"] = {
        "57914/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57914"}]
    }
    with pytest.raises(ValueError):
        planned_manifest(**kwargs)
    keycloak["State"]["Running"] = True
    keycloak["State"]["Health"]["Status"] = "unhealthy"
    with pytest.raises(ValueError):
        planned_manifest(**kwargs)
    keycloak["State"]["Health"]["Status"] = "healthy"
    assert planned_manifest(**kwargs)["revision"] == TARGET_SHA


def test_private_output_is_exclusive_and_never_follows_symlinks(tmp_path):
    path = tmp_path / "candidate.json"
    write_new(path, {"safe": True})
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_new(path, {"replaced": True})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        write_new(link, {})
    assert json.loads(path.read_text()) == {"safe": True}


def test_stage_cli_is_offline_and_does_not_touch_canonical_state(
    facts, tmp_path, monkeypatch, capsys
):
    from scripts import b2b_interactive, b2b_upgrade_manifest

    kwargs, baseline = facts
    state = tmp_path / "b2b-demo"
    state.mkdir(mode=0o700)
    sentinel = state / "untouched"
    sentinel.write_text("canonical state must stay unchanged")
    points = tmp_path / "b2b-backups"
    points.mkdir(mode=0o700)
    point = points / "v1393-point-simulation"
    point.mkdir(mode=0o700)
    source = kwargs["source"]
    source["state"] = str(state)
    source["version"] = 1
    baseline["services"]["bff"]["environment"] = {
        "PRIVATE_TEST_MARKER": "value-must-not-be-printed"
    }
    write_new(point / "source-compose.json", baseline)
    source["files"]["compose.json"] = hashlib.sha256(
        (point / "source-compose.json").read_bytes()
    ).hexdigest()
    write_new(point / "source-manifest.json", source)
    for name, iid in (("api.iid", kwargs["api"]), ("web.iid", kwargs["web"])):
        path = point / name
        path.write_text(iid)
        path.chmod(0o600)
    monkeypatch.setattr(b2b_interactive, "DEFAULT_STATE", state)

    def no_docker(*args, **kwargs):
        pytest.fail("Offline staging must not call Docker")

    monkeypatch.setattr(b2b_interactive, "command", no_docker)
    monkeypatch.setattr(
        "sys.argv",
        ["prepare", "stage", "--point-dir", str(point), "--direction", "upgrade"],
    )
    assert b2b_upgrade_manifest.main() == 0
    staged = json.loads((point / "upgrade-closed-compose.json").read_text())
    assert staged["services"]["bff"]["image"] == kwargs["api"]
    assert staged["services"]["proxy"]["ports"] == []
    assert list(state.iterdir()) == [sentinel]
    assert sentinel.read_text() == "canonical state must stay unchanged"
    assert "value-must-not-be-printed" not in capsys.readouterr().out


def test_stage_cli_refuses_foreign_point_without_writes(tmp_path, monkeypatch, capsys):
    from scripts import b2b_upgrade_manifest

    monkeypatch.setattr(
        "sys.argv",
        ["prepare", "stage", "--point-dir", str(tmp_path), "--direction", "upgrade"],
    )
    assert b2b_upgrade_manifest.main() == 1
    assert not list(tmp_path.iterdir())
    assert "NO-GO" in capsys.readouterr().out
