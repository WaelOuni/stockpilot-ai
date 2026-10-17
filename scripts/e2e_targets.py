"""Fail closed unless E2E connections identify our own disposable Docker services.

Docker inspection stays private: only generic refusal messages leave this module.
The local Docker daemon is a trusted boundary, not an adversarial tenant service.
"""
import json
import os
from pathlib import Path
import subprocess

from psycopg.conninfo import conninfo_to_dict
from psycopg import ProgrammingError
from pymongo.errors import ConfigurationError
from pymongo.uri_parser import parse_uri

PROJECT = "stockpilot-e2e-tests"
DATABASE = "stockpilot_e2e"
COMPOSE = Path(__file__).resolve().parents[1] / "docker" / "compose.e2e.yml"


def endpoints(mongo_uri, postgres_uri):
    try:
        if not mongo_uri.startswith("mongodb://"):
            raise ValueError
        mongo = parse_uri(mongo_uri)
        postgres = conninfo_to_dict(postgres_uri)
        nodes = mongo["nodelist"]
        if (len(nodes) != 1 or nodes[0][0] != "127.0.0.1" or nodes[0][1] == 27017
                or mongo["database"] not in (None, DATABASE)
                or mongo["options"].get("replicaSet") != "rs0"
                or mongo["options"].get("directConnection") is not True
                or "proxyHost" in mongo["options"] or "proxyPort" in mongo["options"]
                or postgres.get("host") != "127.0.0.1" or "hostaddr" in postgres
                or "service" in postgres or postgres.get("dbname") != DATABASE
                or int(postgres.get("port", 5432)) == 5432):
            raise ValueError
        return {"mongo": nodes[0][1], "postgres": int(postgres["port"])}
    except (ValueError, TypeError, KeyError, ProgrammingError, ConfigurationError):
        raise ValueError("Explicit loopback E2E endpoints on dedicated non-default ports are required.") from None


def validate_docker_identity(containers, network, ports):
    """Pure validation of effective daemon state before connecting or mutating."""
    if len(containers) != 2:
        raise ValueError("Expected exactly two dedicated E2E database services.")
    expected_data = {"mongo": {"/data/db", "/data/configdb"}, "postgres": {"/var/lib/postgresql/data"}}
    seen = set()
    for container in containers:
        labels = container["Config"].get("Labels", {})
        service = labels.get("com.docker.compose.service")
        if (service not in expected_data or service in seen
                or labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.project.config_files") != str(COMPOSE)
                or not container["State"].get("Running")
                or container["State"].get("Health", {}).get("Status") != "healthy"):
            raise ValueError("Unrecognized or unhealthy disposable E2E container.")
        seen.add(service)
        settings = container["NetworkSettings"]
        internal = "27017/tcp" if service == "mongo" else "5432/tcp"
        published = {key: value for key, value in (settings.get("Ports") or {}).items() if value}
        if (published != {internal: [{"HostIp": "127.0.0.1", "HostPort": str(ports[service])}]}
                or set(settings["Networks"]) != {PROJECT + "_default"}):
            raise ValueError("E2E connection does not match the dedicated Docker port/network.")
        mounts = container.get("Mounts", [])
        if (any(mount["Type"] != "tmpfs" for mount in mounts)
                or {mount["Destination"] for mount in mounts} - expected_data[service]
                or set(container.get("HostConfig", {}).get("Tmpfs", {})) != expected_data[service]):
            raise ValueError("E2E database storage must be disposable tmpfs only.")
    if (network.get("Labels", {}).get("com.docker.compose.project") != PROJECT
            or network.get("Name") != PROJECT + "_default"
            or network.get("Driver") != "bridge"
            or set(network.get("Containers", {})) != {container["Id"] for container in containers}):
        raise ValueError("E2E network is shared or does not belong to the disposable project.")


def verify_docker_targets(mongo_uri, postgres_uri):
    if os.environ.get("PGHOSTADDR") or os.environ.get("PGSERVICE"):
        raise ValueError("E2E refuses inherited PostgreSQL address/service overrides.")
    ports = endpoints(mongo_uri, postgres_uri)
    try:
        result = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"],
                                check=True, capture_output=True, text=True, timeout=15)
        identifiers = result.stdout.split()
        if len(identifiers) != 2:
            raise ValueError
        containers = json.loads(subprocess.run(["docker", "inspect", *identifiers],
            check=True, capture_output=True, text=True, timeout=15).stdout)
        network = json.loads(subprocess.run(["docker", "network", "inspect", PROJECT + "_default"],
            check=True, capture_output=True, text=True, timeout=15).stdout)[0]
        validate_docker_identity(containers, network, ports)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, IndexError):
        raise ValueError("E2E Docker identity could not be proven; no initialization is permitted.") from None


def verify_mongo_identity(client):
    names = set(client.list_database_names())
    if names - {"admin", "config", "local", DATABASE}:
        raise ValueError("E2E Mongo server contains unrelated databases.")
    if "demo_instance" in client[DATABASE].list_collection_names():
        raise ValueError("E2E Mongo server contains a protected DEMO marker.")


def verify_postgres_identity(connection):
    names = {row[0] for row in connection.execute("SELECT datname FROM pg_database").fetchall()}
    if names - {"postgres", "template0", "template1", DATABASE}:
        raise ValueError("E2E PostgreSQL server contains unrelated databases.")
    if connection.execute("SELECT to_regclass('public.demo_instance')").fetchone()[0] is not None:
        raise ValueError("E2E PostgreSQL database contains a protected DEMO marker.")
