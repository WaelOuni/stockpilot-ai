import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock
import pytest
from langgraph.checkpoint.memory import MemorySaver

from stockpilot import main


def test_lifespan_initializes_order_index_once(monkeypatch) -> None:
    repository = MagicMock()
    monkeypatch.setattr(main, "order_repository", repository)
    closed = []

    @asynccontextmanager
    async def fake_checkpointer(uri):
        try:
            yield MemorySaver()
        finally:
            closed.append(True)

    monkeypatch.setattr(main, "open_checkpointer", fake_checkpointer)

    async def start():
        async with main.lifespan(main.app):
            repository.initialize.assert_called_once_with()
            assert main.app.state.stockpilot_workflow is not None

    asyncio.run(start())
    assert closed == [True]
    assert main.app.state.stockpilot_workflow is None


def test_index_initialization_failure_blocks_startup(monkeypatch) -> None:
    repository = MagicMock()
    repository.initialize.side_effect = RuntimeError("index initialization failed")
    monkeypatch.setattr(main, "order_repository", repository)

    async def start():
        async with main.lifespan(main.app):
            pytest.fail("startup should fail")

    with pytest.raises(RuntimeError, match="index initialization failed"):
        asyncio.run(start())


def test_checkpoint_failure_blocks_startup_without_memory_fallback(monkeypatch):
    monkeypatch.setattr(main, "order_repository", MagicMock())

    @asynccontextmanager
    async def failed_checkpointer(uri):
        raise RuntimeError("checkpoint database unavailable")
        yield

    monkeypatch.setattr(main, "open_checkpointer", failed_checkpointer)

    async def start():
        async with main.lifespan(main.app):
            pytest.fail("startup should fail")

    with pytest.raises(RuntimeError, match="checkpoint database unavailable"):
        asyncio.run(start())
    assert main.app.state.stockpilot_workflow is None


@pytest.mark.parametrize("override", [None, "postgresql://localhost/separate"])
def test_lifespan_uses_checkpoint_override_or_existing_postgres(monkeypatch, override):
    monkeypatch.setattr(main, "order_repository", MagicMock())
    monkeypatch.setattr(main.settings, "langgraph_checkpoint_db_uri", override)
    uris = []

    @asynccontextmanager
    async def fake_checkpointer(uri):
        uris.append(uri)
        yield MemorySaver()

    monkeypatch.setattr(main, "open_checkpointer", fake_checkpointer)

    async def start():
        async with main.lifespan(main.app):
            pass

    asyncio.run(start())
    assert uris == [main.settings.postgres_url if override is None else override]
