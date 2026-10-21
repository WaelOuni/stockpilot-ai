"""Safety contracts for the only canonical operator entrypoint (no Docker I/O)."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from scripts.tests.canonical_backup import (
    ARCHIVES,
    attest,
    digest,
    paired_backup,
    restore,
)
from scripts.tests.canonical_demo import COMPOSE, CanonicalDemo
from stockpilot.application.ports.bff_security import BffUnavailable
from stockpilot.infrastructure.config.bff_settings import BffSettings

ROOT = Path(__file__).resolve().parents[1]


def owner(tmp_path):
    project = "stockpilot-bff-test-" + "a" * 12
    records = {
        "Id": "source-physical-id",
        "Image": "sha256:fixed",
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "stockpilot.audit.purpose": "v1384-ephemeral",
                "com.docker.compose.project.config_files": str(COMPOSE),
                "com.docker.compose.service": "mongo",
            }
        },
        "Mounts": [],
        "HostConfig": {"PortBindings": {"27017/tcp": [{"HostIp": "127.0.0.1"}]}},
        "State": {"Running": True, "Status": "running"},
    }

    def command(args, env=None):
        return (
            json.dumps([records])
            if args[:2] == ["docker", "inspect"]
            else "source-physical-id"
        )

    return SimpleNamespace(
        compose=["docker", "compose", "-f", str(COMPOSE), "-p", project],
        env={"BFF_TEST_PROJECT": project},
        directory=tmp_path,
        command=command,
        validate_mount=Mock(),
    ), records


@pytest.mark.parametrize(
    "key,value",
    [
        ("com.docker.compose.project", "protected"),
        ("stockpilot.audit.purpose", "historical"),
        ("com.docker.compose.project.config_files", "/historical/compose.yml"),
        ("com.docker.compose.service", "foreign"),
    ],
)
def test_provenance_refuses_foreign_resource_before_archive(tmp_path, key, value):
    instance, record = owner(tmp_path)
    record["Config"]["Labels"][key] = value
    with pytest.raises(RuntimeError):
        attest(instance)


@pytest.mark.parametrize("address", ["0.0.0.0", "::", "", "192.168.1.1"])
def test_management_bindings_are_loopback_only(tmp_path, address):
    instance, record = owner(tmp_path)
    record["HostConfig"]["PortBindings"]["27017/tcp"][0]["HostIp"] = address
    with pytest.raises(RuntimeError):
        attest(instance)


def test_exact_owned_provenance_is_required(tmp_path):
    instance, _ = owner(tmp_path)
    assert attest(instance)["mongo"]["Id"] == "source-physical-id"


@pytest.mark.parametrize(
    "name",
    [
        "stockpilot",
        "stockpilot_demo",
        "stockpilot_candidate",
        "stockpilot_b2b_test_x",
        "",
        None,
    ],
)
def test_backup_database_guard_precedes_io(tmp_path, name):
    instance, _ = owner(tmp_path)
    db = SimpleNamespace(name=name)
    with pytest.raises((RuntimeError, TypeError)):
        paired_backup(instance, db, tmp_path / "backup", expected_pending=[])
    assert not (tmp_path / "backup").exists()


def test_restore_source_identity_cannot_be_used_as_target(tmp_path):
    instance, _ = owner(tmp_path)
    manifest = {
        "source_project": instance.env["BFF_TEST_PROJECT"],
        "mongo_database": "stockpilot_b2b_test_" + "a" * 32,
    }
    database = Mock(name="target")
    database.name = manifest["mongo_database"]
    with pytest.raises(RuntimeError):
        restore(instance, database, tmp_path, manifest)


def test_restore_occupied_target_refused(tmp_path):
    instance, _ = owner(tmp_path)
    manifest = {
        "source_project": "independent-project",
        "source_ids": {"mongo": "foreign"},
        "mongo_database": "stockpilot_b2b_test_" + "a" * 32,
    }
    db = Mock()
    db.name = manifest["mongo_database"]
    db.list_collection_names.return_value = ["historical"]
    with pytest.raises(RuntimeError):
        restore(instance, db, tmp_path, manifest)


def test_digest_rejects_symlink_and_missing_archive(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(RuntimeError):
        digest(missing)
    target = tmp_path / "data"
    target.write_bytes(b"synthetic")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(RuntimeError):
        digest(link)
    assert len(digest(target)) == 64


def test_mounts_are_readonly_and_confined_to_runtime(tmp_path):
    instance = object.__new__(CanonicalDemo)
    instance.directory = tmp_path
    with pytest.raises(RuntimeError):
        instance.validate_mount(
            {"Type": "bind", "Source": "/home/protected/.env", "RW": False}
        )
    with pytest.raises(RuntimeError):
        instance.validate_mount(
            {"Type": "bind", "Source": str(tmp_path / "owned"), "RW": True}
        )
    instance.validate_mount(
        {"Type": "bind", "Source": str(tmp_path / "owned"), "RW": False}
    )
    with pytest.raises(RuntimeError):
        instance.validate_mount({"Type": "device"})


def test_dedicated_volumes_are_not_a_cleanup_pattern(tmp_path):
    instance = object.__new__(CanonicalDemo)
    instance.baseline_volumes = set()
    instance.baseline_volumes = set()
    instance.directory, instance.env, instance.volumes = (
        tmp_path,
        {"BFF_TEST_PROJECT": "owned"},
        set(),
    )
    instance.command = Mock(
        return_value=json.dumps(
            [
                {
                    "Labels": {
                        "com.docker.compose.project": "protected",
                        "stockpilot.audit.purpose": "v1384-ephemeral",
                    }
                }
            ]
        )
    )
    with pytest.raises(RuntimeError):
        instance.validate_mount({"Type": "volume", "Name": "protected-data"})
    assert not instance.volumes


def test_compose_has_one_private_canonical_topology():
    configuration = yaml.safe_load(COMPOSE.read_text())
    assert set(configuration["services"]) == {
        "mongo",
        "test-postgres",
        "keycloak",
        "keycloak-db",
        "bff",
        "proxy",
        "auth-mongo",
        "operator",
    }
    assert configuration["services"]["operator"]["profiles"] == ["operator"]
    assert configuration["services"]["operator"]["read_only"] is True
    for name, service in configuration["services"].items():
        if name not in {"bff", "proxy", "operator"}:
            assert "@sha256:" in service["image"]
        assert service["mem_limit"] and service["cpus"]
        assert service["logging"]["options"]["max-size"] == "5m"
        assert service["restart"] == "no"
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:")
    assert configuration["networks"]["identity"]["internal"]
    assert "ports" not in configuration["services"]["bff"]
    assert "ports" not in configuration["services"]["keycloak"]
    assert "ports" not in configuration["services"]["keycloak-db"]
    assert "ports" not in configuration["services"]["bff"]
    assert configuration["services"]["bff"]["read_only"]


def test_proxy_does_not_expose_historical_or_admin_api():
    config = (ROOT / "docker/b2b/proxy.conf").read_text()
    assert "location / { return 404; }" in config
    assert "location /api/" not in config
    assert "access_log off" in config
    assert "error_log /dev/null crit" in config
    assert "proxy_next_upstream off" in config
    assert "X-Forwarded-Proto https" in config
    assert 'Forwarded ""' in config
    assert "auth.stockpilot.test:57915" in config


def test_invalid_private_ca_fails_closed():
    settings = BffSettings(oidc_ca_file="/nonexistent/private-ca")
    with pytest.raises(BffUnavailable):
        settings.validated_policy()


def test_archives_have_fixed_names_not_manifest_controlled():
    assert ARCHIVES == {
        "mongo": "mongo.archive.gz",
        "test-postgres": "application.dump",
        "keycloak-db": "keycloak.dump",
    }


def test_private_ca_changes_invalidate_policy_without_changing_historical_default(
    tmp_path,
):
    from test_bff_auth import settings

    from scripts.tests.validate_bff_keycloak import make_certificate

    first = settings().validated_policy()
    certificate, _ = make_certificate(tmp_path)
    configured = settings(oidc_ca_file=str(certificate)).validated_policy()
    assert configured != first
    assert settings().validated_policy() == first


def test_bff_http_observability_never_contains_query_or_headers(caplog):
    import logging

    from fastapi.testclient import TestClient

    from stockpilot.bff import create_app

    with caplog.at_level(logging.INFO, logger="stockpilot.bff.http"):
        response = TestClient(create_app(BffSettings(mode="disabled"))).get(
            "/b2b/auth/callback?code=DO-NOT-LOG-CODE&state=DO-NOT-LOG-STATE",
            headers={
                "Authorization": "Bearer DO-NOT-LOG-TOKEN",
                "Cookie": "DO-NOT-LOG-COOKIE",
            },
        )
    assert response.status_code == 503
    records = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "stockpilot.bff.http"
    ]
    assert records[0]["category"] == "oidc" and records[0]["status"] == 503
    assert records[0]["duration_ms"] >= 0
    assert all("DO-NOT-LOG" not in json.dumps(record) for record in records)


def test_cleanup_refuses_image_retag_without_executing_remove():
    instance = object.__new__(CanonicalDemo)
    instance.built_tags = {"owned:revision": "sha256:expected"}
    instance.command = Mock(
        return_value=json.dumps(
            [
                {
                    "Id": "sha256:changed",
                    "Config": {
                        "Labels": {"stockpilot.audit.purpose": "v1384-ephemeral"}
                    },
                }
            ]
        )
    )
    with pytest.raises(RuntimeError):
        instance.cleanup_images()
    assert instance.command.call_count == 1


def test_cleanup_refuses_changed_volume_identity():
    instance = object.__new__(CanonicalDemo)
    instance.volumes = {"owned-data"}
    instance.volume_records = {"owned-data": {"CreatedAt": "original"}}
    instance.command = Mock(
        return_value=json.dumps(
            [
                {
                    "Name": "owned-data",
                    "CreatedAt": "different",
                    "Labels": {
                        "com.docker.compose.project": "owned",
                        "stockpilot.audit.purpose": "v1384-ephemeral",
                    },
                }
            ]
        )
    )
    with pytest.raises(RuntimeError):
        instance.cleanup_volumes()
    assert instance.command.call_count == 1


def test_preexisting_volume_is_never_adopted_even_with_expected_labels():
    instance = object.__new__(CanonicalDemo)
    instance.baseline_volumes = {"preexisting"}
    with pytest.raises(RuntimeError):
        instance.validate_mount({"Type": "volume", "Name": "preexisting"})


@pytest.mark.parametrize(
    "name", ["stockpilot", "stockpilot_demo", "stockpilot_candidate"]
)
def test_restore_never_uses_historical_namespace(tmp_path, name):
    database = SimpleNamespace(name=name)
    with pytest.raises(RuntimeError):
        restore(Mock(), database, tmp_path, {})


def test_corrupt_archive_refused_before_restore_write(tmp_path, monkeypatch):
    from scripts.tests import canonical_backup as backup

    records = {service: {"Id": service, "Image": "fixed"} for service in ARCHIVES}
    monkeypatch.setattr(backup, "attest", lambda _: records)
    monkeypatch.setattr(backup, "pg_facts", lambda *_: {})
    binary = Mock()
    monkeypatch.setattr(backup, "binary", binary)
    instance = SimpleNamespace(
        env={"BFF_TEST_PROJECT": "fresh-copy"},
        command=Mock(return_value="postgres\ntemplate0\ntemplate1"),
    )
    db = Mock()
    db.name = "stockpilot_b2b_test_" + "a" * 32
    db.list_collection_names.return_value = []
    db.client.list_database_names.return_value = ["admin", "config", "local"]
    for filename in ARCHIVES.values():
        (tmp_path / filename).write_bytes(b"corrupted")
    manifest = {
        "source_project": "source",
        "source_ids": {},
        "mongo_database": db.name,
        "images": {service: "fixed" for service in ARCHIVES},
        "archives": {service: {"sha256": "wrong"} for service in ARCHIVES},
    }
    with pytest.raises(RuntimeError, match="checksum"):
        backup.restore(instance, db, tmp_path, manifest)
    binary.assert_not_called()


def test_backup_rejects_running_writer_before_creating_archive(tmp_path, monkeypatch):
    from scripts.tests import canonical_backup as backup

    monkeypatch.setattr(
        backup,
        "attest",
        lambda _: {
            service: {"State": {"Running": service == "bff"}}
            for service in ("bff", "proxy", "keycloak")
        },
    )
    database = SimpleNamespace(name="stockpilot_b2b_test_" + "b" * 32)
    with pytest.raises(RuntimeError, match="stopped"):
        backup.paired_backup(Mock(), database, tmp_path / "backup", expected_pending=[])
    assert not (tmp_path / "backup").exists()


def test_backup_unknown_pending_never_claims_coherent_snapshot(tmp_path, monkeypatch):
    from scripts.tests import canonical_backup as backup

    monkeypatch.setattr(
        backup,
        "attest",
        lambda _: {
            service: {"State": {"Running": False}}
            for service in ("bff", "proxy", "keycloak")
        },
    )
    monkeypatch.setattr(backup, "pg_pending", lambda _: ["unreconciled"])
    database = SimpleNamespace(name="stockpilot_b2b_test_" + "b" * 32)
    with pytest.raises(RuntimeError, match="Pending"):
        backup.paired_backup(Mock(), database, tmp_path / "backup", expected_pending=[])
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize(
    "content", ["[]", "{}", '{"OTHER": "x"}', '{"BFF_MODE": 1}', "{bad"]
)
def test_image_launcher_rejects_invalid_config_without_echo(tmp_path, content):
    from stockpilot.infrastructure.config.b2b_demo_entrypoint import load_configuration

    path = tmp_path / "private.json"
    path.write_text(content)
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match="Private BFF configuration required"):
        load_configuration(path)


def test_image_launcher_requires_private_bounded_file(tmp_path):
    from stockpilot.infrastructure.config.b2b_demo_entrypoint import load_configuration

    path = tmp_path / "private.json"
    path.write_text('{"BFF_MODE":"disabled"}')
    path.chmod(0o644)
    with pytest.raises(RuntimeError):
        load_configuration(path)
    path.chmod(0o600)
    assert load_configuration(path) == {"BFF_MODE": "disabled"}
    path.write_bytes(b"x" * 32769)
    with pytest.raises(RuntimeError):
        load_configuration(path)


@pytest.mark.parametrize("port", ["5191", "5193", "5175", "8003", "27017"])
def test_operator_refuses_management_port_override_before_any_docker_io(tmp_path, port):
    command = Mock()
    with pytest.raises(RuntimeError, match="Fixed disposable management ports"):
        CanonicalDemo(
            tmp_path,
            {"SP_DEMO_MONGO_PORT": port},
            [],
            command,
            tmp_path / "ca",
            tmp_path / "key",
        )
    command.assert_not_called()
