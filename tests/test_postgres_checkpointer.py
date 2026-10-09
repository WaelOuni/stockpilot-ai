import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from stockpilot.infrastructure.persistence.postgres import checkpointer
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory


def test_checkpointer_runs_official_setup_once_and_closes(monkeypatch):
    saver = SimpleNamespace(setup=AsyncMock())
    calls = []

    @asynccontextmanager
    async def from_conn_string(uri, *, serde):
        calls.append((uri, serde))
        yield saver
        calls.append("closed")

    monkeypatch.setattr(
        checkpointer.AsyncPostgresSaver, "from_conn_string", from_conn_string
    )

    async def run():
        async with checkpointer.open_checkpointer("postgresql://localhost/test") as result:
            assert result is saver
            saver.setup.assert_awaited_once()
        assert calls[-1] == "closed"

    asyncio.run(run())
    serde = calls[0][1]
    assert isinstance(serde, JsonPlusSerializer)
    state = {
        "intent": Intent.ORDER,
        "category": ProductCategory.DRILL,
        "quantity": 2,
        "pending_order": {"operation_id": "saved-op", "status": "PENDING_CONFIRMATION"},
    }
    assert serde.loads_typed(serde.dumps_typed(state)) == state


def test_setup_failure_closes_connection_and_propagates(monkeypatch):
    closed = []

    @asynccontextmanager
    async def from_conn_string(uri, *, serde):
        try:
            yield SimpleNamespace(setup=AsyncMock(side_effect=RuntimeError("setup failed")))
        finally:
            closed.append(True)

    monkeypatch.setattr(
        checkpointer.AsyncPostgresSaver, "from_conn_string", from_conn_string
    )

    async def run():
        async with checkpointer.open_checkpointer("postgresql://localhost/test"):
            pytest.fail("setup must fail")

    with pytest.raises(RuntimeError, match="setup failed"):
        asyncio.run(run())
    assert closed == [True]
