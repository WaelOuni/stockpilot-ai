"""Real disposable PG/Mongo: failure, stale workers, retry and authorization.

No fixture can target an application or DEMO database. Model failures below
are injections, not evidence about a real Ollama model's quality or latency.
"""
import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from test_tenant_assistant_integration import assistant  # noqa: F401
from test_tenant_knowledge_integration import environment  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context, db, orders  # noqa: F401
from test_tenant_stock_operations_mongo_integration import stock  # noqa: F401
from stockpilot.application.assistant_runtime import Observation, observation
from stockpilot.application.ports.tenant_knowledge import TenantDataConflict, TenantDataOutcomeUnknown, TenantDataUnavailable
from stockpilot.application.resource_errors import ModelProviderError, ModelTimeoutError
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.infrastructure.persistence.postgres.tenant_assistant import PgTenantAssistant
from stockpilot.infrastructure.persistence.postgres.tenant_checkpointer import PgTenantCheckpointSaver

pytestmark = pytest.mark.integration
MESSAGE = "Quelle est la procédure du document synthétique ?"


def args(a):
    service, conv, env = a
    return REQUESTS["a1"], context(env.orders), conv


def durable(a):
    return a[0].read(*args(a))["items"][0]


def business(db):
    return {n: list(db[n].find({}).sort("_id", 1)) for n in (
        "b2b_orders", "b2b_order_operations", "b2b_products", "b2b_stock_movements")}


@pytest.mark.parametrize("failure,code", [(ModelProviderError(500), "provider_http_500"),
    (ModelProviderError(503), "provider_http_503"), (ModelTimeoutError("read_timeout"), "read_timeout")])
def test_failed_consultation_retry_keeps_original_identity_and_never_orders(assistant, db, failure, code):
    a, conv, e = assistant
    before = business(db)
    a.llm.classify_intent.side_effect = failure
    with pytest.raises(TenantDataUnavailable):
        asyncio.run(a.send(*args(assistant), "original", MESSAGE, read_only=True))
    failed = durable(assistant)
    assert failed["status"] == "pending" and failed["execution"]["state"] == "failed"
    assert failed["execution"]["code"] == code and failed["operation"] is None
    original = a.store.turn(*args(assistant), "original")
    assert original["input"]["read_only"] is True and original["action"] is None
    for key in ("original", "accidental-new-message"):
        with pytest.raises(TenantDataConflict):
            asyncio.run(a.send(*args(assistant), key, MESSAGE, read_only=True))
    with pytest.raises(TenantDataConflict):
        asyncio.run(a.retry(*args(assistant), "original"))
    first_id = failed["execution"]["attempt_id"]
    with pytest.raises(TenantDataUnavailable):
        asyncio.run(a.retry(*args(assistant), "original", expected_attempt=first_id))
    second = durable(assistant)
    assert second["execution"]["attempt_id"] != first_id
    with pytest.raises(TenantDataConflict):
        asyncio.run(a.retry(*args(assistant), "original", expected_attempt=first_id))
    a.llm.classify_intent.side_effect = None
    completed = asyncio.run(a.retry(*args(assistant), "original", expected_attempt=second["execution"]["attempt_id"]))
    assert completed["status"] == "completed" and completed["execution"]["state"] == "completed"
    calls = a.llm.classify_intent.await_count
    assert asyncio.run(a.retry(*args(assistant), "original")) == completed
    assert a.llm.classify_intent.await_count == calls
    final = a.store.turn(*args(assistant), "original")
    for key in ("turn_id", "input", "created_at"):
        assert final[key] == original[key]
    assert business(db) == before


@pytest.mark.parametrize("reason", ["client_cancelled", "server_cancelled", "global_budget_exceeded"])
def test_interrupted_read_has_durable_failure_and_no_late_checkpoint(assistant, db, reason):
    a, conv, e = assistant
    before = business(db)
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)
    a.llm.classify_intent.side_effect = slow
    a.read_budget_seconds = .4 if reason == "global_budget_exceeded" else 5
    async def run():
        with observation(Observation()) as obs:
            async def cancel():
                await asyncio.sleep(.08)
                obs.cancel(reason)
            cancellation = asyncio.create_task(cancel()) if reason != "global_budget_exceeded" else None
            try:
                with pytest.raises(TenantDataUnavailable):
                    await a.send(*args(assistant), "original", MESSAGE, read_only=True)
            finally:
                if cancellation:
                    await cancellation
    asyncio.run(run())
    turn = durable(assistant)
    assert turn["status"] == "pending" and turn["execution"]["state"] == "failed"
    assert turn["execution"]["code"] == reason
    assert business(db) == before


@pytest.mark.parametrize("stage", ["after_begin", "before_checkpoint", "after_checkpoint", "before_finish"])
def test_process_loss_is_unknown_and_explicit_retry_uses_same_turn(assistant, db, stage):
    a, conv, e = assistant
    before = business(db)
    class ProcessLost(BaseException):
        pass
    owner = PgTenantAssistant if stage in {"after_begin", "before_finish"} else PgTenantCheckpointSaver
    name = "begin" if stage == "after_begin" else "finish_read" if stage == "before_finish" else "put"
    original = getattr(owner, name)
    def interrupted(self, *values, **kwargs):
        if stage in {"after_begin", "after_checkpoint"}:
            original(self, *values, **kwargs)
        raise ProcessLost()
    # Inject lost execution at actual boundaries. No direct status UPDATE and
    # no checkpoint deletion; this simulates loss, not an actual OS kill.
    with patch.object(owner, name, interrupted):
        with pytest.raises(ProcessLost):
            asyncio.run(a.send(*args(assistant), "crashed", MESSAGE, read_only=True))
    turn = durable(assistant)
    identity = turn["execution"]["attempt_id"]
    assert turn["execution"]["state"] == "unknown" and turn["execution"]["attempt_id"] == identity
    calls = a.llm.classify_intent.await_count
    resumed = asyncio.run(a.resume(*args(assistant)))["items"][0]
    assert resumed["status"] == "pending" and a.llm.classify_intent.await_count == calls
    result = asyncio.run(a.retry(*args(assistant), "crashed", expected_attempt=identity))
    assert result["turn_id"] == "crashed" and result["status"] == "completed"
    assert business(db) == before


def test_stale_worker_cannot_write_checkpoint_finish_or_failure(assistant):
    a, conv, e = assistant
    old, new = uuid4().hex, uuid4().hex
    a.store.begin(*args(assistant), "original", "message", {"message": MESSAGE, "read_only": True}, read_attempt=old)
    stale = a.store.bind_read(*args(assistant), "original", old)
    checkpoint = empty_checkpoint()
    config = stale.put(stale.config(), checkpoint, {"source": "input", "step": -1}, {})
    # New transaction after loss of the original advisory connection.
    a.store.restart_read(*args(assistant), "original", new, old)
    with pytest.raises(TenantDataConflict):
        stale.put(stale.config(), empty_checkpoint(), {"source": "loop", "step": 0}, {})
    with pytest.raises(TenantDataConflict):
        stale.put_writes(config, [("question", "stale")], "old-task")
    with pytest.raises(TenantDataConflict):
        a.store.finish_read(*args(assistant), "original", old, {"text": "stale"})
    with pytest.raises(TenantDataConflict):
        a.store.fail_read(*args(assistant), "original", old, "read_timeout", "classification")
    assert durable(assistant)["execution"]["attempt_id"] == new


def test_committed_finish_followed_by_lost_response_never_downgrades_completed(assistant):
    a, conv, e = assistant
    finish = PgTenantAssistant.finish_read
    def lost(self, *values):
        finish(self, *values)
        raise ConnectionError("synthetic lost acknowledgment")
    with patch.object(PgTenantAssistant, "finish_read", lost):
        with pytest.raises(TenantDataOutcomeUnknown):
            asyncio.run(a.send(*args(assistant), "original", MESSAGE, read_only=True))
    assert durable(assistant)["status"] == "completed"


def test_revocation_prevents_failure_writer_from_bypassing_current_rights(assistant, db):
    a, conv, e = assistant
    async def revoked(*args, **kwargs):
        db.b2b_memberships.update_one({"membership_id": "m-a1"}, {"$set": {"status": "suspended"}})
        raise ModelProviderError(500)
    a.llm.classify_intent.side_effect = revoked
    with pytest.raises(TenantDataOutcomeUnknown):
        asyncio.run(a.send(*args(assistant), "original", MESSAGE, read_only=True))
    with pytest.raises(AuthorizationDenied):
        durable(assistant)


def test_real_advisory_connection_loss_fences_the_old_running_graph(assistant, db):
    """Terminate only this disposable fixture's identified advisory backend."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import psycopg
    from stockpilot.application.dto.intent_result import IntentResult
    from stockpilot.application.services.tenant_assistant import TenantAssistant
    a, conv, e = assistant
    started, release = threading.Event(), threading.Event()
    before = business(db)
    async def blocked(*values, **kwargs):
        started.set()
        await asyncio.to_thread(release.wait, 15)
        return IntentResult(intent='UNKNOWN')
    a.llm.classify_intent.side_effect = blocked
    peer = TenantAssistant(a.store, a.catalogue, a.orders, a.retrieval,
        SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(intent='UNKNOWN'))))
    with ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(lambda: asyncio.run(a.send(*args(assistant), 'original', MESSAGE, read_only=True)))
        try:
            assert started.wait(10)
            running = durable(assistant)
            assert running['execution']['state'] == 'running'
            with pytest.raises(TenantDataConflict):
                asyncio.run(peer.retry(*args(assistant), 'original', expected_attempt=running['execution']['attempt_id']))
            key = repr(a.store._key(context(e.orders), conv))
            with psycopg.connect(e.uri) as connection:
                assert connection.execute('SELECT current_database()').fetchone()[0] == 'stockpilot_ci'
                pids = connection.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted "
                    "AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                    "AND classid=((hashtextextended(%s,7)>>32)&4294967295)::oid "
                    "AND objid=(hashtextextended(%s,7)&4294967295)::oid AND objsubid=1", (key,key)).fetchall()
                assert len(pids) == 1 and pids[0][0] != connection.info.backend_pid
                assert connection.execute('SELECT pg_terminate_backend(%s)', (pids[0][0],)).fetchone()[0]
            assert durable(assistant)['execution']['state'] == 'unknown'
            completed = asyncio.run(peer.retry(*args(assistant), 'original', expected_attempt=running['execution']['attempt_id']))
            assert completed['status'] == 'completed'
        finally:
            release.set()
        with pytest.raises((TenantDataConflict, TenantDataUnavailable)):
            original.result(timeout=15)
    assert durable(assistant) == completed
    assert business(db) == before
