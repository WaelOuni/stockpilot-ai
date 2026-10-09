from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.observability import event


logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_checkpointer(uri: str) -> AsyncIterator[AsyncPostgresSaver]:
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[
            (Intent.__module__, Intent.__name__),
            (ProductCategory.__module__, ProductCategory.__name__),
        ],
    )
    initialized = False
    try:
        async with AsyncPostgresSaver.from_conn_string(uri, serde=serde) as saver:
            await saver.setup()
            initialized = True
            event(logger, "checkpoint_store_initialized")
            yield saver
    except Exception:
        if not initialized:
            event(logger, "checkpoint_store_startup_failed",
                  level=logging.ERROR, exc_info=True)
        raise
    finally:
        if initialized:
            event(logger, "checkpoint_store_closed")
