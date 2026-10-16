from pathlib import Path
from types import SimpleNamespace

import pytest

from integration_targets import (
    UnsafeTestTarget, mongo_test_client, postgres_test_schema, validate_mongo_test_uri,
    validate_postgres_test_uri,
)
from stockpilot.infrastructure.config.settings import Settings

APP = Settings(
    mongo_uri="mongodb://localhost:27017/?replicaSet=rs0&directConnection=true",
    postgres_url="postgresql://app:secret@localhost:5432/stockpilot",
    langgraph_checkpoint_db_uri=None,
)
MONGO_OK = "mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true"
POSTGRES_OK = "postgresql://ci:ci@127.0.0.1:55432/stockpilot_ci"


class FakeResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class FakeConnection:
    """Records every statement; reads answer from `database` and `marker`."""

    def __init__(self, database="stockpilot_ci", marker=None, fail_on_create=False):
        self.database, self.marker, self.fail_on_create = database, marker, fail_on_create
        self.reads, self.writes = [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query):
        if isinstance(query, str):
            self.reads.append(query)
            return FakeResult(self.database if "current_database" in query else self.marker)
        self.writes.append(repr(query))
        if self.fail_on_create and "CREATE SCHEMA" in self.writes[-1]:
            raise RuntimeError("create failed")
        return FakeResult(None)


def connector(connection):
    calls = []

    def connect(uri, **kwargs):
        calls.append(uri)
        return connection
    connect.calls = calls
    return connect


def forbidden_connect(*args, **kwargs):
    pytest.fail("No connection may be attempted for a refused target")


@pytest.mark.parametrize("environ", [{}, {"POSTGRES_TEST_URI": ""}])
def test_missing_postgres_uri_is_refused_without_connecting(environ):
    with pytest.raises(UnsafeTestTarget, match="POSTGRES_TEST_URI"):
        with postgres_test_schema(environ, APP, connect=forbidden_connect):
            pass


def test_missing_mongo_uri_is_refused_without_client():
    with pytest.raises(UnsafeTestTarget, match="MONGO_TEST_URI"):
        mongo_test_client({}, APP, client_factory=forbidden_connect)


@pytest.mark.parametrize("uri", [
    "postgresql://app:secret@localhost:5432/stockpilot",
    "postgresql://app:secret@localhost:5432/stockpilot_ci",
    "postgresql://app:secret@db.internal:5432/stockpilot_ci",
    "postgresql://ci:ci@127.0.0.1:55432/stockpilot",
    "postgresql://ci:ci@127.0.0.1:55432/stockpilot_demo",
    "postgresql://ci:ci@127.0.0.1:55432/stockpilot_demo_v017",
    "postgresql://ci:ci@127.0.0.1:55432/postgres",
])
def test_application_or_unapproved_postgres_targets_are_refused(uri):
    with pytest.raises(UnsafeTestTarget):
        with postgres_test_schema({"POSTGRES_TEST_URI": uri}, APP, connect=forbidden_connect):
            pass


def test_application_settings_are_never_a_postgres_fallback():
    settings = Settings(postgres_url="postgresql://ci:ci@127.0.0.1:55432/stockpilot_ci",
                        langgraph_checkpoint_db_uri=None)
    with pytest.raises(UnsafeTestTarget, match="application endpoint"):
        validate_postgres_test_uri({"POSTGRES_TEST_URI": POSTGRES_OK}, settings)


@pytest.mark.parametrize("uri", [
    "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true",
    "mongodb://127.0.0.1:27017/",
    "mongodb://127.0.0.1:27027/stockpilot?replicaSet=rs0",
    "mongodb://127.0.0.1:27027/stockpilot_demo_v017?replicaSet=rs0",
])
def test_application_or_protected_mongo_targets_are_refused(uri):
    with pytest.raises(UnsafeTestTarget):
        validate_mongo_test_uri({"MONGO_TEST_URI": uri}, APP)


def test_mongo_target_equal_to_application_settings_is_refused():
    settings = Settings(mongo_uri=MONGO_OK)
    with pytest.raises(UnsafeTestTarget, match="application endpoint"):
        validate_mongo_test_uri({"MONGO_TEST_URI": MONGO_OK}, settings)


def test_mongo_server_hosting_application_databases_is_refused_and_closed():
    class Client:
        closed = False

        def list_database_names(self):
            return ["admin", "stockpilot_demo_v017"]

        def close(self):
            self.closed = True

    client = Client()
    with pytest.raises(UnsafeTestTarget, match="protected"):
        mongo_test_client({"MONGO_TEST_URI": MONGO_OK}, APP, client_factory=lambda *a, **k: client)
    assert client.closed


def test_approved_mongo_target_is_accepted():
    client = SimpleNamespace(list_database_names=lambda: ["admin", "config"])
    assert mongo_test_client({"MONGO_TEST_URI": MONGO_OK}, APP, client_factory=lambda *a, **k: client) is client


def test_mongo_server_with_demo_marker_collection_is_refused():
    class Database:
        def list_collection_names(self, filter=None):
            return ["demo_instance"]

    class Client:
        closed = False

        def list_database_names(self):
            return ["admin", "renamed_database"]

        def __getitem__(self, name):
            return Database()

        def close(self):
            self.closed = True

    client = Client()
    with pytest.raises(UnsafeTestTarget, match="DEMO instance"):
        mongo_test_client({"MONGO_TEST_URI": MONGO_OK}, APP, client_factory=lambda *a, **k: client)
    assert client.closed


@pytest.mark.parametrize("connection", [
    FakeConnection(database="stockpilot"),
    FakeConnection(database="stockpilot_demo_v017"),
    FakeConnection(marker="public.demo_instance"),
])
def test_effective_connection_identity_is_verified_before_any_write(connection):
    with pytest.raises(UnsafeTestTarget):
        with postgres_test_schema({"POSTGRES_TEST_URI": POSTGRES_OK}, APP, connect=connector(connection)):
            pytest.fail("Body must not run for an unverified target")
    assert connection.writes == []


def test_approved_postgres_target_creates_and_drops_only_its_schema():
    connection = FakeConnection()
    connect = connector(connection)
    with postgres_test_schema({"POSTGRES_TEST_URI": POSTGRES_OK}, APP, connect=connect) as conninfo:
        assert "search_path=stockpilot_checkpoint_test_" in conninfo
        assert connection.writes and "CREATE SCHEMA" in connection.writes[0]
    assert connect.calls == [POSTGRES_OK]
    assert [("CREATE SCHEMA" in w, "DROP SCHEMA" in w) for w in connection.writes] == [(True, False), (False, True)]
    schema = {w.split("Identifier('")[1].split("')")[0] for w in connection.writes}
    assert len(schema) == 1 and schema.pop().startswith("stockpilot_checkpoint_test_")


def test_failed_schema_creation_never_drops_anything():
    connection = FakeConnection(fail_on_create=True)
    with pytest.raises(RuntimeError, match="create failed"):
        with postgres_test_schema({"POSTGRES_TEST_URI": POSTGRES_OK}, APP, connect=connector(connection)):
            pytest.fail("Body must not run")
    assert not any("DROP" in w for w in connection.writes)


def test_body_failure_still_drops_the_created_schema():
    connection = FakeConnection()
    with pytest.raises(AssertionError):
        with postgres_test_schema({"POSTGRES_TEST_URI": POSTGRES_OK}, APP, connect=connector(connection)):
            raise AssertionError("test body failed")
    assert sum("DROP SCHEMA" in w for w in connection.writes) == 1


def test_integration_modules_have_no_application_fallback():
    tests = Path(__file__).parent
    for path in [*tests.glob("*integration*.py"), tests / "integration_targets.py"]:
        if path == Path(__file__):
            continue
        source = path.read_text(encoding="utf-8")
        assert 'environ.get("MONGO_TEST_URI",' not in source, path.name
        assert 'environ.get("POSTGRES_TEST_URI",' not in source, path.name
        assert "Settings().postgres_url" not in source.replace("def _settings", ""), path.name
        assert "localhost:27017" not in source, path.name
