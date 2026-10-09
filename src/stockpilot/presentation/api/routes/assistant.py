from fastapi import APIRouter, Depends
import logging

from stockpilot.observability import Timer, bind_context, event, log_context
from stockpilot.telemetry import enrich_span

from stockpilot.presentation.api.dependencies import get_stockpilot_workflow
from stockpilot.presentation.api.schemas.chat import AssistantRequest


router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/assistant")
async def assistant(
    request: AssistantRequest,
    workflow=Depends(get_stockpilot_workflow),
) -> dict:
    timer = Timer()
    with log_context(thread_id=request.thread_id):
        enrich_span(**{"stockpilot.thread_id": request.thread_id})
        event(logger, "assistant_request_started")
        try:
            result = await workflow.ainvoke(
                {"message": request.message},
                config={"configurable": {"thread_id": request.thread_id}},
            )
        except Exception:
            event(logger, "assistant_request_failed", level=logging.ERROR,
                  exc_info=True, duration_ms=timer.duration_ms)
            raise
        pending = result.get("pending_order") or {}
        enrich_span(**{
            "stockpilot.operation_id": pending.get("operation_id"),
            "stockpilot.order_id": result.get("created_order_id"),
            "stockpilot.intent": result.get("intent"),
            "stockpilot.order_status": result.get("order_status"),
        })
        bind_context(intent=result.get("intent"), operation_id=pending.get("operation_id"),
                     order_id=result.get("created_order_id"))
        event(logger, "assistant_request_completed", duration_ms=timer.duration_ms,
              order_status=result.get("order_status"),
              created_order_id=result.get("created_order_id"))
    return result
