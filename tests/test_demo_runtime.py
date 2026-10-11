import pytest

from stockpilot.demo_dataset import DEMO_DATABASE
from stockpilot.demo_runtime import (
    CHECKPOINT_MIGRATION_VERSION,
    CHECKPOINT_TABLES,
    KNOWLEDGE_COLUMNS,
    validate_demo_targets,
    validate_postgres_schema_state,
)
from stockpilot.infrastructure.config.settings import Settings


def demo_settings(**overrides):
    values = {
        "mongo_uri": "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true",
        "mongo_database": DEMO_DATABASE,
        "postgres_url": "postgresql://localhost/stockpilot_demo",
        "langgraph_checkpoint_db_uri": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_demo_targets_require_explicit_dedicated_mongo_and_postgres():
    settings = demo_settings()
    assert validate_demo_targets(settings) == settings.postgres_url


@pytest.mark.parametrize("settings", [
    demo_settings(mongo_database="stockpilot"),
    demo_settings(mongo_uri="mongodb://localhost:27017/stockpilot?replicaSet=rs0"),
    demo_settings(postgres_url="postgresql://localhost/stockpilot"),
    demo_settings(langgraph_checkpoint_db_uri="postgresql://localhost/stockpilot_e2e"),
])
def test_demo_targets_refuse_normal_or_e2e_stores(settings):
    with pytest.raises(ValueError):
        validate_demo_targets(settings)


def valid_schema_state(**overrides):
    state = {
        "database_name": DEMO_DATABASE,
        "tables": CHECKPOINT_TABLES | {"knowledge_chunks"},
        "latest_checkpoint_migration": CHECKPOINT_MIGRATION_VERSION,
        "knowledge_columns": KNOWLEDGE_COLUMNS.copy(),
        "knowledge_vector_format": "vector(768)",
        "source_unique_definition": "UNIQUE NULLS NOT DISTINCT (source)",
        "require_checkpoint": True,
        "require_knowledge": True,
    }
    state.update(overrides)
    validate_postgres_schema_state(**state)


def test_existing_current_demo_schemas_pass_readiness_check():
    valid_schema_state()


@pytest.mark.parametrize("overrides", [
    {"database_name": "stockpilot"},
    {"tables": {"knowledge_chunks"}},
    {"latest_checkpoint_migration": CHECKPOINT_MIGRATION_VERSION - 1},
    {"knowledge_columns": {"embedding": ("USER-DEFINED", "text", "NO")}},
    {"knowledge_vector_format": "vector(1536)"},
    {"source_unique_definition": None},
])
def test_incomplete_or_wrong_postgres_schema_is_refused(overrides):
    with pytest.raises(ValueError):
        valid_schema_state(**overrides)


@pytest.fixture
def fake_preflight(monkeypatch):
    from types import SimpleNamespace
    import stockpilot.demo_runtime as runtime

    state = SimpleNamespace(mongo_identity="local-rs", reference_identity="local-rs",
                            database="stockpilot_demo", postgres_identity="local-pg",
                            schema="public", lifecycle=None, queries=[], collections=[])

    class Mongo:
        def __init__(self, uri, **kwargs):
            self.identity = state.reference_identity if uri == Settings().mongo_uri else state.mongo_identity
            self.admin = self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def command(self, name):
            assert name == "replSetGetConfig"
            return {"config": {"settings": {"replicaSetId": self.identity}}}

    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def transaction(self):
            return self
        def execute(self, query):
            state.queries.append(query)
            values = {
                "SELECT system_identifier FROM pg_control_system()": state.postgres_identity,
                "SELECT current_database()": state.database,
                "SELECT current_schema()": state.schema,
                "SELECT count(*) FROM knowledge_chunks": 0,
                "SET TRANSACTION READ ONLY": None,
            }
            assert query in values
            return SimpleNamespace(fetchone=lambda: (values[query],))

    monkeypatch.setattr(runtime, "MongoClient", Mongo)
    monkeypatch.setattr(runtime, "guarded_database", lambda *args: type("Database", (), {"name": "stockpilot_demo", "__getitem__": lambda self, name: SimpleNamespace(find_one=lambda *args: state.lifecycle)})())
    monkeypatch.setattr(runtime, "validate_existing_schema", lambda *args, **kwargs: state.collections.append("schema"))
    monkeypatch.setattr(runtime, "workspace_summary", lambda *args: {})
    monkeypatch.setattr(runtime, "postgres_server_identity", lambda uri: "local-pg")
    monkeypatch.setattr("scripts.prepare_demo_postgres.demo_uri", lambda: "postgresql://local/stockpilot_demo")
    monkeypatch.setattr(runtime.psycopg, "connect", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(runtime, "verify_postgres_schema", lambda *args, **kwargs: None)
    return runtime, state


def test_actual_demo_instances_pass_with_alias_override(fake_preflight):
    runtime, state = fake_preflight
    result = runtime.preflight_demo(demo_settings(mongo_uri="mongodb://alias/?replicaSet=rs0"))
    assert result.rag_document_count == 0
    assert "SELECT current_database()" in state.queries


def test_unexpected_mongo_instance_is_refused_before_collection_access(fake_preflight):
    runtime, state = fake_preflight
    state.mongo_identity = "unrelated-rs0"
    with pytest.raises(ValueError, match="Unexpected Mongo"):
        runtime.preflight_demo(demo_settings(mongo_uri="mongodb://unexpected/?replicaSet=rs0"))
    assert state.collections == []
    assert state.queries == []


def test_missing_mongo_identity_fails_closed(fake_preflight):
    runtime, state = fake_preflight
    state.reference_identity = None
    with pytest.raises(ValueError, match="identity is unavailable"):
        runtime.preflight_demo(demo_settings())
    assert state.collections == []


@pytest.mark.parametrize("target", ["postgres_url", "langgraph_checkpoint_db_uri"])
def test_unexpected_actual_postgres_instance_is_refused(fake_preflight, target):
    runtime, state = fake_preflight
    original_connect = runtime.psycopg.connect

    def connect(uri, **kwargs):
        state.postgres_identity = "unexpected-pg" if "unexpected" in uri else "local-pg"
        return original_connect(uri, **kwargs)

    runtime.psycopg.connect = connect
    with pytest.raises(ValueError, match="Unexpected PostgreSQL"):
        runtime.preflight_demo(demo_settings(**{target: "postgresql://unexpected/stockpilot_demo"}))
    if target == "postgres_url":
        assert "SELECT count(*) FROM knowledge_chunks" not in state.queries


@pytest.mark.parametrize("database", ["stockpilot", "stockpilot_e2e"])
def test_actual_postgres_database_is_checked_even_with_valid_uri(fake_preflight, database):
    runtime, state = fake_preflight
    state.database = database
    with pytest.raises(ValueError, match="connected PostgreSQL database"):
        runtime.preflight_demo(demo_settings())
    assert "SELECT count(*) FROM knowledge_chunks" not in state.queries


def test_postgres_uri_options_are_preserved_and_wrong_actual_namespace_refused(fake_preflight):
    runtime, state = fake_preflight
    original_connect = runtime.psycopg.connect
    checked = []

    def connect(uri, **kwargs):
        checked.append(kwargs["options"])
        assert "-c search_path=other" in kwargs["options"]
        assert kwargs["options"].endswith("-c default_transaction_read_only=on")
        state.schema = "other"
        return original_connect(uri, **kwargs)

    runtime.psycopg.connect = connect
    settings = demo_settings(postgres_url="dbname=stockpilot_demo host=local options='-c search_path=other'")
    with pytest.raises(ValueError, match="namespace must be public"):
        runtime.preflight_demo(settings)
    assert checked
    assert "SELECT count(*) FROM knowledge_chunks" not in state.queries


@pytest.mark.parametrize("marker", [
    {"mongo_identity": "other", "postgres_identity": "local-pg"},
    {"mongo_identity": "local-rs", "postgres_identity": "other"},
])
def test_lifecycle_marker_cannot_move_to_other_instances(fake_preflight, marker):
    runtime, state = fake_preflight
    state.lifecycle = marker
    with pytest.raises(ValueError, match="lifecycle marker belongs to another"):
        runtime.preflight_demo(demo_settings())
