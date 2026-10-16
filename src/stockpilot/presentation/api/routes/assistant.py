from typing import Annotated
from hashlib import sha256
import json

from fastapi import APIRouter, Depends, Request
from pydantic import StringConstraints
import logging

from stockpilot.observability import Timer, bind_context, event, log_context
from stockpilot.telemetry import enrich_span

from stockpilot.presentation.api.auth import require_customer_identity
from stockpilot.presentation.api.dependencies import get_stockpilot_workflow
from stockpilot.presentation.api.schemas.chat import AssistantRequest
from stockpilot.presentation.api.input_protection import validate_conversation_input


router = APIRouter()
logger = logging.getLogger(__name__)


def customer_thread_key(customer_id: str, thread_id: str) -> str:
    encoded = json.dumps([customer_id, thread_id], ensure_ascii=True, separators=(",", ":"))
    return "customer-thread-v1:" + sha256(encoded.encode("utf-8")).hexdigest()


@router.post("/assistant")
async def assistant(
    request: AssistantRequest,
    http_request: Request,
    customer_id: Annotated[str, Depends(require_customer_identity)],
    workflow=Depends(get_stockpilot_workflow),
) -> dict:
    validate_conversation_input(http_request, request)
    timer = Timer()
    with log_context(thread_id=request.thread_id):
        enrich_span(**{"stockpilot.thread_id": request.thread_id})
        event(logger, "assistant_request_started")
        try:
            result = await workflow.ainvoke(
                {"message": request.message, "customer_id": customer_id},
                config={"configurable": {
                    "thread_id": customer_thread_key(customer_id, request.thread_id),
                }},
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
    if result.get("intent") != "PRODUCT_ADVICE" or result.get("confirmation_handled"):
        result.pop("recommendations", None)
        result.pop("advice_status", None)
    return result
