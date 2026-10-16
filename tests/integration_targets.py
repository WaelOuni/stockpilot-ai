"""Explicit, verified targets for integration fixtures.

An integration fixture may only write to a dedicated test service named by
MONGO_TEST_URI / POSTGRES_TEST_URI. There is no fallback to application settings,
and a URI alone is not trusted: the effective connection is inspected before any
write. Pure validators take their inputs as arguments so refusals are unit-tested
with fakes and never touch a real database.
"""
from contextlib import contextmanager
import os
import re
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri

# Default ports of the development Compose stack and the usual application names.
APPLICATION_PORTS = {27017, 5432}
PROTECTED_MONGO_DATABASES = {"stockpilot", "stockpilot_e2e"}
POSTGRES_TEST_DATABASE = re.compile(r"stockpilot_(ci|test)(_[a-z0-9]+)*")
CHECKPOINT_SCHEMA = re.compile(r"stockpilot_checkpoint_test_[0-9a-f]{32}")


class UnsafeTestTarget(RuntimeError):
    """The requested integration target is not an approved dedicated test service."""


def application_endpoints(settings):
    """Endpoints the application itself would use; never valid test targets."""
    mongo = {(host.lower(), port) for host, port in parse_uri(settings.mongo_uri)["nodelist"]}
    postgres = set()
    for uri in (settings.postgres_url, settings.langgraph_checkpoint_db_uri):
        if uri:
            info = conninfo_to_dict(uri)
            postgres.add((info.get("host", "localhost").lower(), int(info.get("port", 5432)),
                          info.get("dbname", "")))
    return mongo, postgres


def _require(environ, name):
    value = environ.get(name)
    if not value:
        raise UnsafeTestTarget(f"{name} must name a dedicated test service; no default is used")
    return value


def validate_mongo_test_uri(environ, settings):
    uri = _require(environ, "MONGO_TEST_URI")
    parsed = parse_uri(uri)
    app_mongo, _ = application_endpoints(settings)
    for host, port in parsed["nodelist"]:
        if (host.lower(), port) in app_mongo or port in APPLICATION_PORTS:
            raise UnsafeTestTarget("MONGO_TEST_URI points at an application endpoint")
    if parsed["database"] in PROTECTED_MONGO_DATABASES or str(parsed["database"] or "").startswith(
        "stockpilot_demo"
    ):
        raise UnsafeTestTarget("MONGO_TEST_URI names a protected database")
    return uri


def validate_postgres_test_uri(environ, settings):
    uri = _require(environ, "POSTGRES_TEST_URI")
    info = conninfo_to_dict(uri)
    host, port, name = info.get("host", "localhost").lower(), int(info.get("port", 5432)), info.get("dbname", "")
    _, app_postgres = application_endpoints(settings)
    if (host, port, name) in app_postgres or port in APPLICATION_PORTS:
        raise UnsafeTestTarget("POSTGRES_TEST_URI points at an application endpoint")
    if not POSTGRES_TEST_DATABASE.fullmatch(name):
        raise UnsafeTestTarget("POSTGRES_TEST_URI database is not an approved test database")
    return uri


def verify_mongo_identity(client):
    """Check the effective server before any write: replica set and no protected data."""
    names = set(client.list_database_names())
    protected = names & PROTECTED_MONGO_DATABASES | {n for n in names if n.startswith("stockpilot_demo")}
    if protected:
        raise UnsafeTestTarget("Mongo server hosts protected application databases")
    # DEMO deployments write a `demo_instance` marker collection in their database.
    for name in names - {"admin", "config", "local"}:
        if client[name].list_collection_names(filter={"name": "demo_instance"}):
            raise UnsafeTestTarget("Mongo server hosts a DEMO instance")


def verify_postgres_identity(connection, uri):
    """Check the effective connection (not the URI text) before CREATE/DROP."""
    expected = conninfo_to_dict(uri).get("dbname", "")
    actual = connection.execute("SELECT current_database()").fetchone()[0]
    if actual != expected or not POSTGRES_TEST_DATABASE.fullmatch(actual):
        raise UnsafeTestTarget("Effective PostgreSQL database is not the approved test database")
    # DEMO deployments create public.demo_instance (bootstrap/002_instance.sql).
    if connection.execute("SELECT to_regclass('public.demo_instance')").fetchone()[0] is not None:
        raise UnsafeTestTarget("PostgreSQL target carries a DEMO instance marker")


def _settings():
    from stockpilot.infrastructure.config.settings import Settings
    return Settings()


def mongo_test_client(environ=None, settings=None, client_factory=MongoClient):
    uri = validate_mongo_test_uri(os.environ if environ is None else environ, settings or _settings())
    client = client_factory(uri, serverSelectionTimeoutMS=5000)
    try:
        verify_mongo_identity(client)
    except BaseException:
        client.close()
        raise
    return client


@contextmanager
def postgres_test_schema(environ=None, settings=None, connect=psycopg.connect):
    """Yield a conninfo scoped to a fresh schema; drop only the schema created here."""
    uri = validate_postgres_test_uri(os.environ if environ is None else environ, settings or _settings())
    schema = "stockpilot_checkpoint_test_" + uuid4().hex
    assert CHECKPOINT_SCHEMA.fullmatch(schema)
    with connect(uri, autocommit=True) as connection:
        verify_postgres_identity(connection, uri)
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield make_conninfo(uri, options=f"-c search_path={schema}")
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
