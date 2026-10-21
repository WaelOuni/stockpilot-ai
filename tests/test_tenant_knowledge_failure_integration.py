"""Failure/concurrency and migration proofs on actual disposable PostgreSQL."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import psycopg
import pytest
from integration_targets import postgres_test_schema
from langgraph.checkpoint.base import empty_checkpoint
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from test_tenant_knowledge_integration import (
    REQUESTS,
    V,
    command,
    context,
    ingest,
    open_saver,
    save,
    search,
)
from test_tenant_knowledge_integration import db as db  # noqa: PLC0414
from test_tenant_knowledge_integration import environment as environment  # noqa: PLC0414
from test_tenant_knowledge_integration import orders as orders  # noqa: PLC0414
from test_tenant_knowledge_integration import stock as stock  # noqa: PLC0414

from stockpilot.application.ports.tenant_knowledge import (
    TenantDataConflict,
    TenantDataNotFound,
    TenantDataOutcomeUnknown,
    TenantDataUnavailable,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.infrastructure.persistence.postgres.tenant_migrations import (
    PostgresMigrationError,
    PostgresTenantMigrations,
    registry,
)

pytestmark = pytest.mark.integration


def test_early_pending_writes_are_invisible_and_deleted_without_resurrection(
    environment,
):
    e = environment
    convo, saver = open_saver(e)
    cp = empty_checkpoint()
    future = saver.config(cp["id"])
    saver.put_writes(future, [("channel", "private pending")], "early-task")
    assert saver.get_tuple(future) is None
    assert saver.get_tuple(saver.config()) is None
    saver.delete_thread(convo.conversation_id)
    with pytest.raises(TenantDataNotFound):
        saver.put_writes(future, [("channel", "late")], "late-task")
    with pytest.raises(TenantDataNotFound):
        saver.put(saver.config(), cp, {}, {})
    with psycopg.connect(e.uri) as c:
        assert (
            c.execute(
                sql.SQL(
                    "SELECT count(*) FROM {} WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s"
                ).format(e.repo.t("checkpoint_writes")),
                ("org-a", "a1", convo.conversation_id),
            ).fetchone()[0]
            == 0
        )


def test_generator_uses_existing_llm_port_signature(environment):
    from stockpilot.application.services.tenant_retrieval import TenantRetrieval

    e = environment
    ingest(e)

    class StrictGenerator:
        prompt = None

        async def generate(self, prompt, response_format=None):
            self.prompt = prompt
            return "grounded answer"

    generator = StrictGenerator()
    service = TenantRetrieval(
        e.repo, e.embeddings, generator, model="synthetic-768", version="v1"
    )
    assert (
        asyncio.run(service.execute(REQUESTS["a1"], context(e.orders), "question"))
        == "grounded answer"
    )
    assert "<documents>\nA confidential\n</documents>" in generator.prompt


def test_real_langgraph_interrupt_resume_after_new_saver(environment):
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    e = environment
    convo, saver = open_saver(e)

    class State(TypedDict):
        value: str

    def step(state):
        return {"value": interrupt("approval")}

    def graph(checkpointer):
        b = StateGraph(State)
        b.add_node("step", step)
        b.add_edge(START, "step")
        b.add_edge("step", END)
        return b.compile(checkpointer=checkpointer)

    initial = graph(saver).invoke({"value": "A"}, saver.config())
    assert initial["__interrupt__"]
    reopened = e.conversations.bind(
        REQUESTS["a1"], context(e.orders), convo.conversation_id
    )
    result = graph(reopened).invoke(Command(resume="approved"), reopened.config())
    assert result["value"] == "approved"
    assert len(list(reopened.list(reopened.config()))) >= 3


@pytest.mark.parametrize("stage", ["embedding", "generation"])
def test_provider_http_errors_are_sanitized(environment, stage):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from stockpilot.application.services.tenant_retrieval import TenantRetrieval

    e = environment
    ingest(e)
    error = httpx.ReadTimeout("sensitive provider details")
    generator = SimpleNamespace(generate=AsyncMock(return_value="answer"))
    if stage == "embedding":
        e.embeddings.embed.side_effect = error
    else:
        generator.generate.side_effect = error
    retrieval = TenantRetrieval(
        e.repo, e.embeddings, generator, model="synthetic-768", version="v1"
    )
    with pytest.raises(TenantDataUnavailable) as failure:
        asyncio.run(retrieval.execute(REQUESTS["a1"], context(e.orders), "question"))
    assert "sensitive" not in str(failure.value)


def test_retrieval_rechecks_revocation_after_sql_before_llm(
    environment, db, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from stockpilot.application.services.tenant_retrieval import TenantRetrieval

    e = environment
    ingest(e)
    original = e.repo.search

    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        db.b2b_memberships.update_one(
            {"membership_id": "m-a1"}, {"$set": {"status": "suspended"}}
        )
        return result

    monkeypatch.setattr(e.repo, "search", revoke)
    llm = SimpleNamespace(generate=AsyncMock())
    retrieval = TenantRetrieval(
        e.repo, e.embeddings, llm, model="synthetic-768", version="v1"
    )
    with pytest.raises(AuthorizationDenied):
        asyncio.run(retrieval.execute(REQUESTS["a1"], context(e.orders), "question"))
    llm.generate.assert_not_awaited()


def test_checkpoint_context_organization_change_and_unknown_identity_refused(
    environment,
):
    from dataclasses import replace

    e = environment
    convo, saver = open_saver(e)
    save(saver)
    ctx = context(e.orders)
    forged = replace(ctx, principal=replace(ctx.principal, organization_id="org-b"))
    for request, provided in (
        (REQUESTS["a1"], forged),
        (object(), ctx),
        (REQUESTS["a1"], None),
    ):
        with pytest.raises(AuthorizationDenied):
            e.conversations.bind(request, provided, convo.conversation_id)


def test_checkpoint_put_then_delete_serializes_and_leaves_no_rows(environment):
    e = environment
    convo, saver = open_saver(e)
    first = save(saver)

    def put():
        try:
            return save(saver, "second")
        except TenantDataNotFound:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        p = pool.submit(put)
        d = pool.submit(saver.delete_thread, convo.conversation_id)
        p.result(timeout=10)
        d.result(timeout=10)
    with pytest.raises(TenantDataNotFound):
        saver.get_tuple(first)
    with psycopg.connect(e.uri) as c:
        assert (
            c.execute(
                sql.SQL(
                    "SELECT count(*) FROM {} WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s"
                ).format(e.repo.t("checkpoints")),
                ("org-a", "a1", convo.conversation_id),
            ).fetchone()[0]
            == 0
        )


def test_exact_title_query_cannot_leak_foreign_collisions(environment):
    e = environment
    ingest(e)
    ingest(e, command(content="B incompatible"), "admin-b")
    rows = e.repo.documents_by_title(REQUESTS["a1"], context(e.orders), "Same title")
    assert len(rows) == 1 and rows[0].organization_id == "org-a"


def test_generation_model_is_server_controlled(environment):
    with pytest.raises(TenantDataConflict):
        ingest(environment, replace(command(), embedding_model="forged-model"))
    assert environment.embeddings.embed.await_count == 0


def test_ingestion_partial_embeddings_leave_no_visible_partial_chunks(environment):
    e = environment
    e.embeddings.embed.side_effect = [V, TimeoutError("sensitive")]
    with pytest.raises(TenantDataUnavailable):
        ingest(e, command(content="A" * 4500))
    assert search(e) == []
    with psycopg.connect(e.uri) as c:
        assert (
            c.execute(
                sql.SQL("SELECT count(*) FROM {}").format(e.repo.t("chunks"))
            ).fetchone()[0]
            == 0
        )
    assert (
        e.repo.ingestion(
            REQUESTS["admin-a"], context(e.orders, "admin-a"), "same-operation"
        ).status
        == "failed"
    )


def test_mid_publication_sql_error_rolls_back_all_chunks(environment, monkeypatch):
    e = environment
    original = psycopg.Connection.execute
    writes = [0]

    def fail(connection, query, *args, **kwargs):
        result = original(connection, query, *args, **kwargs)
        if (
            isinstance(query, sql.Composed)
            and "INSERT INTO" in query.as_string(connection)
            and '"chunks"' in query.as_string(connection)
        ):
            writes[0] += 1
            if writes[0] == 2:
                raise psycopg.OperationalError("synthetic interrupted publication")
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(psycopg.Connection, "execute", fail)
        with pytest.raises(TenantDataOutcomeUnknown):
            ingest(e, command(content="A" * 4500))
    assert search(e) == []
    assert (
        e.repo.ingestion(
            REQUESTS["admin-a"], context(e.orders, "admin-a"), "same-operation"
        ).status
        == "pending"
    )
    with psycopg.connect(e.uri) as c:
        assert (
            c.execute(
                sql.SQL("SELECT count(*) FROM {}").format(e.repo.t("chunks"))
            ).fetchone()[0]
            == 0
        )
    assert ingest(e, command(content="A" * 4500)).status == "ready"
    assert len(search(e, limit=20)) == 3


@pytest.mark.parametrize("commit_number", [1, 2])
def test_lost_commit_response_reconciles_same_key_without_duplicate_chunks(
    environment, monkeypatch, commit_number
):
    e = environment
    original = psycopg.Connection.commit
    count = [0]

    def lost(connection):
        result = original(connection)
        count[0] += 1
        if count[0] == commit_number:
            raise psycopg.OperationalError(
                "synthetic lost response after actual commit"
            )
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(psycopg.Connection, "commit", lost)
        with pytest.raises(TenantDataOutcomeUnknown):
            ingest(e)
    durable = e.repo.ingestion(
        REQUESTS["admin-a"], context(e.orders, "admin-a"), "same-operation"
    )
    assert durable.status == ("pending" if commit_number == 1 else "ready")
    assert ingest(e).status == "ready"
    assert len(search(e)) == 1
    with psycopg.connect(e.uri) as c:
        assert (
            c.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE organization_id=%s").format(
                    e.repo.t("chunks")
                ),
                ("org-a",),
            ).fetchone()[0]
            == 1
        )


def test_concurrent_same_key_and_optimistic_update_are_safe(environment):
    e = environment
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ingest(e), range(2)))
    assert results[0] == results[1]

    def update(n):
        try:
            return ingest(
                e,
                command(
                    operation="update-" + str(n),
                    content="version-" + str(n),
                    revision=1,
                ),
            )
        except TenantDataConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, range(2)))
    assert len([r for r in results if r is not None]) == 1
    assert (
        e.repo.document(REQUESTS["a1"], context(e.orders), "same-document").revision
        == 2
    )


def test_revocation_before_pg_commit_aborts_publication(environment, db, monkeypatch):
    e = environment
    original = e.repo.current
    calls = [0]

    def current(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 5:
            db.b2b_memberships.update_one(
                {"membership_id": "m-admin-a"}, {"$set": {"status": "suspended"}}
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(e.repo, "current", current)
    with pytest.raises(AuthorizationDenied):
        ingest(e)
    assert calls[0] == 5
    assert search(e) == []
    with psycopg.connect(e.uri) as c:
        assert (
            c.execute(
                sql.SQL("SELECT status FROM {} WHERE organization_id=%s").format(
                    e.repo.t("ingestions")
                ),
                ("org-a",),
            ).fetchone()[0]
            == "pending"
        )
        assert (
            c.execute(
                sql.SQL("SELECT count(*) FROM {}").format(e.repo.t("chunks"))
            ).fetchone()[0]
            == 0
        )


def test_corrupt_checkpoint_encoding_refused_and_schema_drift_is_not_empty(environment):
    e = environment
    _, saver = open_saver(e)
    config = save(saver)
    with psycopg.connect(e.uri) as c:
        c.execute(
            sql.SQL(
                "UPDATE {} SET checkpoint_type='pickle' WHERE organization_id=%s"
            ).format(e.repo.t("checkpoints")),
            ("org-a",),
        )
    with pytest.raises(TenantDataUnavailable):
        saver.get_tuple(config)
    with psycopg.connect(e.uri) as c:
        c.execute(
            sql.SQL("ALTER TABLE {} DROP COLUMN embedding").format(e.repo.t("chunks"))
        )
    with pytest.raises(TenantDataUnavailable):
        search(e)


def test_checkpoint_config_injection_parent_collision_and_pending_write_replay(
    environment,
):
    e = environment
    _, saver = open_saver(e)
    config = save(saver)
    checkpoint = saver.get_tuple(config).checkpoint
    checkpoint["channel_values"] = {"forged": "changed"}
    with pytest.raises(TenantDataConflict):
        saver.put(saver.config(), checkpoint, {"source": "input", "step": 0}, {})
    for value in ("organization_id", "user_id", "customer_id"):
        forged = saver.config()
        forged["configurable"][value] = "other"
        with pytest.raises(ValueError):
            saver.get_tuple(forged)
    forged = saver.config()
    forged["configurable"]["checkpoint_ns"] = "foreign-subgraph"
    with pytest.raises(TenantDataNotFound):
        saver.get_tuple(forged)
    missing = saver.config("unknown")
    with pytest.raises(TenantDataNotFound):
        saver.put(missing, empty_checkpoint(), {}, {})
    saver.put_writes(config, [("channel", "first")], "task")
    saver.put_writes(config, [("channel", "second")], "task")
    assert saver.get_tuple(config).pending_writes == [("task", "channel", "first")]
    assert list(saver.list(saver.config(), before=config)) == []


def test_execution_serialization_and_delete_cannot_race_active_graph(environment):
    e = environment
    convo, saver = open_saver(e)
    save(saver)
    ctx = context(e.orders)
    with e.conversations.execution(REQUESTS["a1"], ctx, convo.conversation_id):  # noqa: SIM117
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                e.conversations.delete, REQUESTS["a1"], ctx, convo.conversation_id
            )
            with pytest.raises(TenantDataConflict):
                future.result(timeout=10)
    e.conversations.delete(REQUESTS["a1"], ctx, convo.conversation_id)
    with pytest.raises(TenantDataNotFound):
        saver.get_tuple(saver.config())


@pytest.mark.parametrize("drift", ["checksum", "version", "dimension", "index"])
def test_migration_checksum_version_typmod_index_drift_refused(environment, drift):
    e = environment
    with psycopg.connect(e.uri) as c:
        if drift == "checksum":
            c.execute(
                sql.SQL("UPDATE {} SET checksum='wrong'").format(
                    e.repo.t("schema_migrations")
                )
            )
        elif drift == "version":
            c.execute(
                sql.SQL("UPDATE {} SET version=99 WHERE version=1").format(
                    e.repo.t("schema_migrations")
                )
            )
        elif drift == "dimension":
            c.execute(
                sql.SQL(
                    "ALTER TABLE {} ALTER COLUMN embedding TYPE public.vector(512)"
                ).format(e.repo.t("chunks"))
            )
        else:
            c.execute(
                sql.SQL("CREATE INDEX injected_global_title ON {} (title)").format(
                    e.repo.t("documents")
                )
            )
    with pytest.raises(PostgresMigrationError):
        e.runner.status()
    with pytest.raises(TenantDataUnavailable):
        search(e)


def test_migration_plan_reapply_lock_and_transactional_rollback(
    environment, monkeypatch
):
    e = environment
    assert e.runner.plan() == []
    before = e.runner.status()
    with psycopg.connect(e.uri) as c:
        name, identity = e.runner.target(c)
    e.runner.apply(database=name, system_identifier=identity)
    assert e.runner.status() == before
    with pytest.raises(PostgresMigrationError):
        e.runner.apply(database="stockpilot_demo", system_identifier=identity)
    with psycopg.connect(e.uri) as c:
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("stockpilot-b2b-migrations:" + e.schema,),
        )
        with pytest.raises(PostgresMigrationError):
            e.runner.apply(database=name, system_identifier=identity)
    with postgres_test_schema() as uri:
        schema = conninfo_to_dict(uri)["options"].split("=")[1]
        runner = PostgresTenantMigrations(uri, schema)
        assert runner.plan() == [
            (version, checksum) for version, checksum, _ in registry()
        ]
        original = psycopg.Connection.execute

        def broken(connection, query, *args, **kwargs):
            result = original(connection, query, *args, **kwargs)
            if isinstance(query, str) and "CREATE TABLE documents" in query:
                raise psycopg.OperationalError("synthetic DDL interruption")
            return result

        with monkeypatch.context() as scoped:
            scoped.setattr(psycopg.Connection, "execute", broken)
            with pytest.raises(PostgresMigrationError):
                runner.apply(database=name, system_identifier=identity)
        assert runner.status() == []
        runner.apply(database=name, system_identifier=identity)
        assert [row[:2] for row in runner.status()] == [
            (v, h) for v, h, _ in registry()
        ]


def test_candidate_database_on_test_server_is_refused(environment):
    e = environment
    name = "stockpilot_candidate_guard_test"
    with psycopg.connect(e.uri, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with pytest.raises(PostgresMigrationError):
                e.runner.target(c)
        finally:
            c.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
