import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from psycopg.conninfo import conninfo_to_dict


spec = importlib.util.spec_from_file_location(
    "init_e2e_env", Path(__file__).resolve().parents[1] / "scripts" / "init_e2e_env.py",
)
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


@pytest.mark.parametrize("mongo_uri,mongo_database,postgres_uri", [
    ("mongodb://localhost/stockpilot", "stockpilot_e2e", "dbname=stockpilot_e2e"),
    ("mongodb://localhost", "stockpilot", "dbname=stockpilot_e2e"),
    ("mongodb://localhost", "stockpilot_e2e", "dbname=stockpilot"),
    ("mongodb://localhost", "stockpilot_e2e", "dbname=stockpilot_e2e_backup"),
    ("mongodb://localhost", "production_e2e", "dbname=stockpilot_e2e"),
    ("mongodb://localhost/stockpilot%5Fe2e_bad", "stockpilot_e2e", "dbname=stockpilot_e2e"),
])
def test_e2e_rejects_non_dedicated_targets(mongo_uri, mongo_database, postgres_uri):
    with pytest.raises(ValueError):
        e2e.validate_targets(mongo_uri, mongo_database, postgres_uri)


def test_e2e_accepts_exact_dedicated_resources():
    e2e.validate_targets(
        "mongodb://localhost/stockpilot_e2e?authSource=admin",
        "stockpilot_e2e", "dbname=stockpilot_e2e",
    )


def test_e2e_environment_overrides_all_development_stores(monkeypatch):
    monkeypatch.delenv("E2E_MONGO_URI", raising=False)
    monkeypatch.delenv("E2E_POSTGRES_URL", raising=False)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost/?replicaSet=rs0")
    monkeypatch.setenv("MONGO_DATABASE", "stockpilot")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/stockpilot")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DB_URI", "postgresql://localhost/stockpilot")
    monkeypatch.setenv("OTEL_ENABLED", "false")
    monkeypatch.setenv("LOG_FILE", "")
    settings = e2e.configure_environment()
    assert settings.mongo_database == "stockpilot_e2e"
    assert conninfo_to_dict(settings.postgres_url)["dbname"] == "stockpilot_e2e"
    assert settings.langgraph_checkpoint_db_uri == settings.postgres_url
    assert settings.otel_enabled is False


def test_e2e_reset_checks_actual_database_before_any_write():
    settings = MagicMock()
    settings.mongo_uri = "mongodb://localhost"
    settings.mongo_database = "stockpilot_e2e"
    settings.postgres_url = "dbname=stockpilot_e2e"
    settings.langgraph_checkpoint_db_uri = settings.postgres_url
    mongo = MagicMock()
    mongo.name = "stockpilot"
    connection = MagicMock()
    with pytest.raises(ValueError):
        e2e.reset_stores(settings, connection, mongo)
    mongo.orders.delete_many.assert_not_called()
    mongo.products.delete_many.assert_not_called()
    connection.execute.assert_not_called()


def test_e2e_reset_rejects_actual_non_e2e_postgres_database():
    settings = MagicMock()
    settings.mongo_uri = "mongodb://localhost"
    settings.mongo_database = "stockpilot_e2e"
    settings.postgres_url = "dbname=stockpilot_e2e"
    settings.langgraph_checkpoint_db_uri = settings.postgres_url
    mongo = MagicMock()
    mongo.name = "stockpilot_e2e"
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = ("stockpilot",)
    with pytest.raises(ValueError):
        e2e.reset_stores(settings, connection, mongo)
    mongo.orders.delete_many.assert_not_called()
    mongo.products.delete_many.assert_not_called()
    connection.transaction.assert_not_called()


def test_e2e_concurrent_initializer_refuses_before_creation(monkeypatch):
    admin = MagicMock()
    admin.execute.return_value.fetchone.return_value = (False,)
    context = MagicMock()
    context.__enter__.return_value = admin
    monkeypatch.setattr(e2e.psycopg, "connect", MagicMock(return_value=context))
    with pytest.raises(RuntimeError, match="Another E2E"):
        with e2e.locked_database("dbname=stockpilot_e2e"):
            pytest.fail("The lock should prevent entering the reset")
    assert admin.execute.call_count == 1


def test_e2e_reset_only_touches_named_resources(monkeypatch):
    settings = MagicMock()
    settings.mongo_uri = "mongodb://localhost"
    settings.mongo_database = "stockpilot_e2e"
    settings.postgres_url = "dbname=stockpilot_e2e"
    settings.langgraph_checkpoint_db_uri = settings.postgres_url
    mongo = MagicMock()
    mongo.name = "stockpilot_e2e"
    mongo.client.admin.command.return_value = {"setName": "rs0", "isWritablePrimary": True}
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = ("stockpilot_e2e",)
    from unittest.mock import AsyncMock
    monkeypatch.setattr(e2e, "setup_checkpoints", AsyncMock())
    monkeypatch.setattr(e2e, "MongoOrderRepository", MagicMock())
    provision = MagicMock()
    monkeypatch.setattr(e2e, "prepare_unique_sku_index", provision)

    e2e.reset_stores(settings, connection, mongo)

    provision.assert_called_once_with(mongo.products)
    mongo.orders.delete_many.assert_called_once_with({})
    mongo.products.delete_many.assert_called_once_with({})
    assert mongo.products.insert_one.call_args.args[0]["stock"] == 10
    mongo.client.drop_database.assert_not_called()
    reset_sql = connection.execute.call_args.args[0].as_string()
    assert reset_sql == (
        'TRUNCATE "public"."checkpoints", "public"."checkpoint_blobs", '
        '"public"."checkpoint_writes"'
    )


def test_e2e_refuses_external_checkpoint_configuration():
    settings = MagicMock()
    settings.mongo_uri = "mongodb://localhost"
    settings.mongo_database = "stockpilot_e2e"
    settings.postgres_url = "dbname=stockpilot_e2e"
    settings.langgraph_checkpoint_db_uri = "dbname=stockpilot"
    with pytest.raises(ValueError, match="checkpoints"):
        e2e.validate_active_settings(settings)
