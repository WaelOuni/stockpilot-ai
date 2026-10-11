"""Read-only isolation checks for the integrated synthetic demo runtime."""

import os
from dataclasses import dataclass
from typing import Any

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import conninfo_to_dict
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from stockpilot.demo_dataset import DEFAULT_SEED, DEMO_DATABASE, generate_demo_dataset
from stockpilot.demo_persistence import (
    guarded_database,
    validate_demo_target,
    validate_existing_schema,
)
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.demo_workspace import workspace_summary, WORKSPACE_STATE_ID


CHECKPOINT_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}
CHECKPOINT_MIGRATION_VERSION = len(AsyncPostgresSaver.MIGRATIONS) - 1
KNOWLEDGE_COLUMNS = {
    "id": ("bigint", "int8", "NO"),
    "content": ("text", "text", "NO"),
    "source": ("text", "text", "YES"),
    "embedding": ("USER-DEFINED", "vector", "NO"),
}
KnowledgeColumns = dict[str, tuple[str, str, str]]
PostgresSchemaFacts = tuple[
    str, set[str], int | None, KnowledgeColumns, str | None, str | None,
]


@dataclass(frozen=True)
class DemoPreflight:
    mongo_summary: dict[str, Any]
    rag_document_count: int


def resolve_demo_settings() -> Settings:
    """Resolve once; ignore DEV database selections without mutating os.environ."""
    from scripts.prepare_demo_postgres import demo_uri

    overrides = {
        "mongo_uri": os.environ.get("STOCKPILOT_DEMO_MONGO_URI"),
        "postgres_url": os.environ.get("STOCKPILOT_DEMO_POSTGRES_URL"),
        "langgraph_checkpoint_db_uri": os.environ.get("STOCKPILOT_DEMO_CHECKPOINT_URI"),
    }
    if any(value is not None and not value.strip() for value in overrides.values()):
        raise ValueError("Demo connection overrides must not be empty; details are withheld.")
    values = {
        "mongo_database": DEMO_DATABASE,
        "postgres_url": overrides["postgres_url"] or "postgresql://localhost/stockpilot_demo",
        "langgraph_checkpoint_db_uri": overrides["langgraph_checkpoint_db_uri"],
    }
    if overrides["mongo_uri"] is not None:
        values["mongo_uri"] = overrides["mongo_uri"]
    settings = Settings(**values)
    validate_demo_targets(settings)
    if overrides["postgres_url"] is None:
        settings = Settings(**{
            **values, "mongo_uri": settings.mongo_uri, "postgres_url": demo_uri(),
        })
    validate_demo_targets(settings)
    return settings


def demo_connection_environment(settings: Settings) -> dict[str, str]:
    """Pin every child to the resolved connections, including private overrides."""
    checkpoint = validate_demo_targets(settings)
    return {
        "MONGO_DATABASE": DEMO_DATABASE,
        "POSTGRES_URL": settings.postgres_url,
        "LANGGRAPH_CHECKPOINT_DB_URI": checkpoint,
        "STOCKPILOT_DEMO_MONGO_URI": settings.mongo_uri,
        "STOCKPILOT_DEMO_POSTGRES_URL": settings.postgres_url,
        "STOCKPILOT_DEMO_CHECKPOINT_URI": checkpoint,
    }


def validate_demo_targets(settings: Settings) -> str:
    """Require every effective data store to name only the dedicated demo DB."""
    if settings.mongo_database != DEMO_DATABASE:
        raise ValueError("MONGO_DATABASE must be exactly stockpilot_demo.")
    validate_demo_target(settings.mongo_uri, DEMO_DATABASE)

    try:
        postgres_database = conninfo_to_dict(settings.postgres_url).get("dbname")
        checkpoint_uri = settings.langgraph_checkpoint_db_uri or settings.postgres_url
        checkpoint_database = conninfo_to_dict(checkpoint_uri).get("dbname")
    except (TypeError, ValueError, psycopg.Error):
        raise ValueError("PostgreSQL connection settings are invalid; URI details are withheld.") from None
    if postgres_database != DEMO_DATABASE:
        raise ValueError("POSTGRES_URL must explicitly select stockpilot_demo.")
    if checkpoint_database != DEMO_DATABASE:
        raise ValueError("The effective checkpoint URI must explicitly select stockpilot_demo.")
    return checkpoint_uri


def validate_postgres_schema_state(
    *,
    database_name: str,
    tables: set[str],
    latest_checkpoint_migration: int | None,
    knowledge_columns: KnowledgeColumns,
    knowledge_vector_format: str | None,
    source_unique_definition: str | None,
    require_checkpoint: bool,
    require_knowledge: bool,
) -> None:
    if database_name != DEMO_DATABASE:
        raise ValueError("The connected PostgreSQL database is not stockpilot_demo.")
    if require_checkpoint:
        missing = CHECKPOINT_TABLES - tables
        if missing:
            raise ValueError("Demo checkpoint schema is incomplete; no PostgreSQL setup was run.")
        if latest_checkpoint_migration != CHECKPOINT_MIGRATION_VERSION:
            raise ValueError(
                "Demo checkpoint schema is not at the installed migration version; "
                "no PostgreSQL setup was run."
            )
    if require_knowledge:
        if "knowledge_chunks" not in tables:
            raise ValueError("Demo RAG schema is missing knowledge_chunks; no migration was run.")
        if any(knowledge_columns.get(name) != definition
               for name, definition in KNOWLEDGE_COLUMNS.items()):
            raise ValueError("Demo RAG schema is incompatible; no migration was run.")
        if knowledge_vector_format != "vector(768)":
            raise ValueError("Demo RAG vector dimension is incompatible; no migration was run.")
        normalized = " ".join((source_unique_definition or "").lower().split())
        if not normalized.startswith("unique nulls not distinct (source)"):
            raise ValueError("Demo RAG source constraint is missing or incompatible; no migration was run.")


def _postgres_schema_facts(
    connection,
) -> PostgresSchemaFacts:
    database_name = connection.execute("SELECT current_database()").fetchone()[0]
    tables = {
        row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        ).fetchall()
    }

    latest_migration = None
    if "checkpoint_migrations" in tables:
        row = connection.execute(
            "SELECT v FROM checkpoint_migrations ORDER BY v DESC LIMIT 1"
        ).fetchone()
        latest_migration = row[0] if row is not None else None

    knowledge_columns = {}
    knowledge_vector_format = None
    source_unique_definition = None
    if "knowledge_chunks" in tables:
        knowledge_columns = {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                "SELECT column_name, data_type, udt_name, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'knowledge_chunks'"
            ).fetchall()
        }
        row = connection.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'public.knowledge_chunks'::regclass "
            "AND attname = 'embedding' AND NOT attisdropped"
        ).fetchone()
        knowledge_vector_format = row[0] if row is not None else None
        row = connection.execute(
            "SELECT contype, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'public.knowledge_chunks'::regclass "
            "AND conname = 'knowledge_chunks_source_unique'"
        ).fetchone()
        if row is not None and row[0] == "u":
            source_unique_definition = row[1]

    return (
        database_name, tables, latest_migration, knowledge_columns,
        source_unique_definition, knowledge_vector_format,
    )


def verify_postgres_schema(
    connection,
    *,
    require_checkpoint: bool,
    require_knowledge: bool,
) -> None:
    facts = _postgres_schema_facts(connection)
    validate_postgres_schema_state(
        database_name=facts[0],
        tables=facts[1],
        latest_checkpoint_migration=facts[2],
        knowledge_columns=facts[3],
        knowledge_vector_format=facts[5],
        source_unique_definition=facts[4],
        require_checkpoint=require_checkpoint,
        require_knowledge=require_knowledge,
    )


def postgres_server_identity(uri: str) -> str:
    with psycopg.connect(
        uri, connect_timeout=5,
        options=conninfo_to_dict(uri).get("options", "") + " -c default_transaction_read_only=on",
    ) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        identity = connection.execute(
            "SELECT system_identifier FROM pg_control_system()"
        ).fetchone()[0]
        if identity is None:
            raise ValueError("PostgreSQL server identity is unavailable.")
        return str(identity)


def _verify_postgres_uri(
    uri: str, *, require_checkpoint: bool, require_knowledge: bool,
    expected_identity: str,
) -> int | None:
    with psycopg.connect(
        uri, connect_timeout=5,
        options=conninfo_to_dict(uri).get("options", "") + " -c default_transaction_read_only=on",
    ) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            identity = connection.execute(
                "SELECT system_identifier FROM pg_control_system()"
            ).fetchone()[0]
            if str(identity) != expected_identity:
                raise ValueError("Unexpected PostgreSQL demo server; details withheld.")
            if connection.execute("SELECT current_database()").fetchone()[0] != DEMO_DATABASE:
                raise ValueError("The connected PostgreSQL database is not stockpilot_demo.")
            if connection.execute("SELECT current_schema()").fetchone()[0] != "public":
                raise ValueError("Demo PostgreSQL effective namespace must be public.")
            verify_postgres_schema(
                connection,
                require_checkpoint=require_checkpoint,
                require_knowledge=require_knowledge,
            )
            if require_knowledge:
                return connection.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]
    return None


def preflight_demo(settings: Settings) -> DemoPreflight:
    """Verify the mutable Mongo workspace and existing PostgreSQL schemas without writes."""
    checkpoint_uri = validate_demo_targets(settings)
    dataset = generate_demo_dataset(DEFAULT_SEED)
    try:
        # Compare the actual replica-set identity to the configured local server.
        # Only replica-set configuration is read; no DEV database is accessed.
        with MongoClient(Settings().mongo_uri, serverSelectionTimeoutMS=5000,
                         connectTimeoutMS=5000, socketTimeoutMS=15000) as reference:
            config = reference.admin.command("replSetGetConfig")["config"]
            expected_mongo_identity = config.get("settings", {}).get("replicaSetId")
            if expected_mongo_identity is None:
                raise ValueError("Mongo replica-set identity is unavailable.")
        with MongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=15000,
            appname="stockpilot-integrated-demo-preflight",
        ) as client:
            config = client.admin.command("replSetGetConfig")["config"]
            if config.get("settings", {}).get("replicaSetId") != expected_mongo_identity:
                raise ValueError("Unexpected Mongo demo server; details withheld.")
            database = guarded_database(client, DEMO_DATABASE)
            validate_existing_schema(database, complete=True, workspace=True)
            summary = workspace_summary(database, dataset)
            lifecycle = database['demo_manifests'].find_one({'_id': WORKSPACE_STATE_ID})
            if lifecycle and lifecycle.get('mongo_identity') != str(expected_mongo_identity):
                raise ValueError('DEMO lifecycle marker belongs to another Mongo instance.')
    except PyMongoError:
        raise ValueError("Mongo demo preflight failed; connection details are withheld.") from None

    from scripts.prepare_demo_postgres import demo_uri

    try:
        expected_postgres_identity = postgres_server_identity(demo_uri())
    except psycopg.Error:
        raise ValueError("PostgreSQL reference identity verification failed; details withheld.") from None
    if lifecycle and lifecycle.get('postgres_identity') != expected_postgres_identity:
        raise ValueError('DEMO lifecycle marker belongs to another PostgreSQL instance.')
    postgres_targets = [(settings.postgres_url, checkpoint_uri == settings.postgres_url, True)]
    if checkpoint_uri != settings.postgres_url:
        postgres_targets.append((checkpoint_uri, True, False))
    rag_document_count = None
    for uri, require_checkpoint, require_knowledge in postgres_targets:
        try:
            count = _verify_postgres_uri(
                uri,
                require_checkpoint=require_checkpoint,
                require_knowledge=require_knowledge,
                expected_identity=expected_postgres_identity,
            )
            if require_knowledge:
                rag_document_count = count
        except psycopg.Error:
            raise ValueError(
                "PostgreSQL demo preflight failed; connection details are withheld and "
                "no application process was started."
            ) from None

    if rag_document_count is None:
        raise RuntimeError("Read-only preflight did not verify the demo RAG database.")
    return DemoPreflight(
        mongo_summary=summary,
        rag_document_count=rag_document_count,
    )
