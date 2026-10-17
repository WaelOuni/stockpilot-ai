"""No Docker/database access: refuse unsafe E2E targets before initialization."""
from copy import deepcopy
import json
import subprocess
from unittest.mock import MagicMock

import pytest

from scripts import e2e_targets as targets

MONGO = "mongodb://127.0.0.1:27127/?replicaSet=rs0&directConnection=true"
POSTGRES = "postgresql://e2e:e2e@127.0.0.1:55532/stockpilot_e2e"
PORTS = {"mongo": 27127, "postgres": 55532}


def docker_state():
    containers = []
    for service, port, data in (
        ("mongo", "27017/tcp", ["/data/db", "/data/configdb"]),
        ("postgres", "5432/tcp", ["/var/lib/postgresql/data"]),
    ):
        containers.append({
            "Id": service + "-synthetic-id",
            "Config": {"Labels": {"com.docker.compose.project": targets.PROJECT,
                "com.docker.compose.service": service,
                "com.docker.compose.project.config_files": str(targets.COMPOSE)}},
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "NetworkSettings": {"Networks": {targets.PROJECT + "_default": {}},
                "Ports": {port: [{"HostIp": "127.0.0.1", "HostPort": str(PORTS[service])}]}},
            "HostConfig": {"Tmpfs": {path: "" for path in data}},
            "Mounts": [{"Type": "tmpfs", "Destination": path} for path in data],
        })
    network = {"Name": targets.PROJECT + "_default", "Driver": "bridge",
        "Labels": {"com.docker.compose.project": targets.PROJECT},
        "Containers": {container["Id"]: {} for container in containers}}
    return containers, network


@pytest.mark.parametrize("mongo,postgres", [
    (MONGO.replace("27127", "27017"), POSTGRES),
    (MONGO.replace("127.0.0.1", "localhost"), POSTGRES),
    (MONGO.replace("/?", "/stockpilot_demo?"), POSTGRES),
    (MONGO.replace("rs0", "other"), POSTGRES),
    (MONGO.replace("&directConnection=true", ""), POSTGRES),
    (MONGO.replace("directConnection=true", "directConnection=false"), POSTGRES),
    (MONGO + "&proxyHost=192.0.2.1&proxyPort=1080", POSTGRES),
    ("mongodb+srv://unrelated.invalid/stockpilot_e2e", POSTGRES),
    (MONGO.replace("127.0.0.1:27127", "127.0.0.1:27127,127.0.0.1:27128"), POSTGRES),
    (MONGO, POSTGRES.replace("55532", "5432")),
    (MONGO, POSTGRES.replace("127.0.0.1", "localhost")),
    (MONGO, POSTGRES.replace("stockpilot_e2e", "stockpilot_demo")),
    (MONGO, "host=127.0.0.1 hostaddr=192.0.2.1 port=55532 dbname=stockpilot_e2e"),
    (MONGO, "host=127.0.0.1 service=unrelated port=55532 dbname=stockpilot_e2e"),
])
def test_refuse_implicit_remote_default_or_overridden_endpoints(mongo, postgres):
    with pytest.raises(ValueError):
        targets.endpoints(mongo, postgres)


def test_effective_disposable_identity_is_read_only():
    containers, network = docker_state()
    before = deepcopy((containers, network))
    targets.validate_docker_identity(containers, network, PORTS)
    assert (containers, network) == before


@pytest.mark.parametrize("case", ["wrong_project", "wrong_source", "stopped", "unhealthy", "wrong_port",
    "public_port", "extra_port", "foreign_network", "volume", "bind", "missing_tmpfs", "third_container",
    "shared_network", "foreign_network_owner", "duplicate_service"])
def test_refuse_shared_persistent_or_mismatched_docker_resources(case):
    containers, network = docker_state()
    first = containers[0]
    if case == "wrong_project": first["Config"]["Labels"]["com.docker.compose.project"] = "stockpilot-demo-v017"
    elif case == "wrong_source": first["Config"]["Labels"]["com.docker.compose.project.config_files"] = "/unrelated/compose.yml"
    elif case == "stopped": first["State"]["Running"] = False
    elif case == "unhealthy": first["State"]["Health"]["Status"] = "starting"
    elif case == "wrong_port": first["NetworkSettings"]["Ports"]["27017/tcp"][0]["HostPort"] = "27017"
    elif case == "public_port": first["NetworkSettings"]["Ports"]["27017/tcp"][0]["HostIp"] = "0.0.0.0"
    elif case == "extra_port": first["NetworkSettings"]["Ports"]["123/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": "123"}]
    elif case == "foreign_network": first["NetworkSettings"]["Networks"]["stockpilot-demo-v017_data"] = {}
    elif case in {"volume", "bind"}: first["Mounts"][0]["Type"] = case
    elif case == "missing_tmpfs": first["HostConfig"]["Tmpfs"].pop("/data/db")
    elif case == "third_container": containers.append(deepcopy(first))
    elif case == "shared_network": network["Containers"]["foreign-container"] = {}
    elif case == "foreign_network_owner": network["Labels"]["com.docker.compose.project"] = "stockpilot-demo-v017"
    elif case == "duplicate_service": containers[1]["Config"]["Labels"]["com.docker.compose.service"] = "mongo"
    with pytest.raises(ValueError):
        targets.validate_docker_identity(containers, network, PORTS)


def test_docker_desktop_reports_tmpfs_in_host_config_without_mount_entries():
    containers, network = docker_state()
    for container in containers:
        container["Mounts"] = []
    targets.validate_docker_identity(containers, network, PORTS)


def test_verification_inspects_actual_containers_and_network(monkeypatch):
    containers, network = docker_state()
    run = MagicMock(side_effect=[
        subprocess.CompletedProcess([], 0, stdout="mongo-id\npostgres-id\n"),
        subprocess.CompletedProcess([], 0, stdout=json.dumps(containers)),
        subprocess.CompletedProcess([], 0, stdout=json.dumps([network])),
    ])
    monkeypatch.setattr(targets.subprocess, "run", run)
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    targets.verify_docker_targets(MONGO, POSTGRES)
    assert run.call_count == 3
    assert run.call_args_list[1].args[0] == ["docker", "inspect", "mongo-id", "postgres-id"]
    assert run.call_args_list[2].args[0] == ["docker", "network", "inspect", targets.PROJECT + "_default"]


def test_failed_docker_inspection_never_exposes_private_diagnostics(monkeypatch):
    run = MagicMock(side_effect=subprocess.CalledProcessError(1, "docker", stderr="synthetic-private-data"))
    monkeypatch.setattr(targets.subprocess, "run", run)
    with pytest.raises(ValueError, match="identity could not be proven") as error:
        targets.verify_docker_targets(MONGO, POSTGRES)
    assert "synthetic-private-data" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE"])
def test_inherited_libpq_redirection_is_refused_before_docker(monkeypatch, name):
    monkeypatch.setenv(name, "unrelated")
    run = MagicMock()
    monkeypatch.setattr(targets.subprocess, "run", run)
    with pytest.raises(ValueError, match="overrides"):
        targets.verify_docker_targets(MONGO, POSTGRES)
    run.assert_not_called()


def test_real_mongo_identity_refuses_other_databases_and_demo_markers():
    client = MagicMock()
    client.list_database_names.return_value = ["stockpilot_demo"]
    with pytest.raises(ValueError): targets.verify_mongo_identity(client)
    client.list_database_names.return_value = ["admin", "config", "local", targets.DATABASE]
    client[targets.DATABASE].list_collection_names.return_value = ["demo_instance"]
    with pytest.raises(ValueError): targets.verify_mongo_identity(client)
    client[targets.DATABASE].list_collection_names.return_value = ["products"]
    targets.verify_mongo_identity(client)


def test_real_postgres_identity_refuses_other_databases_and_demo_markers():
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = [("stockpilot",)]
    with pytest.raises(ValueError): targets.verify_postgres_identity(connection)
    connection.execute.return_value.fetchall.return_value = [("postgres",), (targets.DATABASE,)]
    connection.execute.return_value.fetchone.return_value = ("demo_instance",)
    with pytest.raises(ValueError): targets.verify_postgres_identity(connection)
    connection.execute.return_value.fetchone.return_value = (None,)
    targets.verify_postgres_identity(connection)
