"""Explicit disposable assistant composition; no historical Settings or startup DDL."""

from types import SimpleNamespace

import psycopg

from stockpilot.application.ports.bff_security import BffUnavailable
from stockpilot.application.services.tenant_assistant import TenantAssistant
from stockpilot.application.services.tenant_retrieval import TenantRetrieval
from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import (
    OllamaEmbeddingAdapter,
)
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
from stockpilot.infrastructure.persistence.postgres.tenant_assistant import (
    PgTenantAssistant,
)
from stockpilot.infrastructure.persistence.postgres.tenant_knowledge import (
    PgTenantKnowledge,
)
from stockpilot.infrastructure.persistence.postgres.tenant_migrations import (
    PostgresMigrationError,
    PostgresTenantMigrations,
)


def compose_assistant(settings, business):
    settings.validate_assistant()
    uri = settings.assistant_postgres_uri.get_secret_value()
    runner = PostgresTenantMigrations(uri, settings.assistant_schema)
    try:
        with psycopg.connect(uri, connect_timeout=5) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            runner.target(connection)
            runner.require(connection)
    except (psycopg.Error, PostgresMigrationError) as error:
        import logging

        logging.getLogger(__name__).warning(
            "Assistant PostgreSQL preflight failed (%s)", type(error).__name__
        )
        raise BffUnavailable("Explicit assistant migrations required.") from None
    config = SimpleNamespace(
        ollama_model=settings.assistant_model,
        ollama_embedding_model=settings.assistant_embedding_model,
        ollama_base_url=settings.assistant_ollama_url,
        ollama_timeout_seconds=settings.assistant_timeout_seconds,
        ollama_max_prompt_bytes=24 * 1024,
    )
    llm = OllamaAdapter(config, max_output_tokens=512)
    embeddings = OllamaEmbeddingAdapter(config)
    knowledge = PgTenantKnowledge(
        uri,
        business.authorization,
        embeddings,
        schema=settings.assistant_schema,
        model=settings.assistant_embedding_model,
        version="v1",
    )
    store = PgTenantAssistant(
        uri, business.authorization, schema=settings.assistant_schema
    )
    return TenantAssistant(
        store,
        business.catalogue,
        business.orders,
        TenantRetrieval(
            knowledge,
            embeddings,
            llm,
            model=settings.assistant_embedding_model,
            version="v1",
        ),
        llm,
    )
