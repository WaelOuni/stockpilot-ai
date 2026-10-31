"""Bounded assistant endpoints on the existing session/Origin/CSRF boundary."""

import asyncio
import inspect
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import Field, StrictBool, StrictStr, field_validator

from stockpilot.application.ports.bff_security import BffUnavailable
from stockpilot.presentation.api.b2b_boundary import worker
from stockpilot.presentation.api.routes.b2b_business import checked, query
from stockpilot.presentation.api.schemas.b2b import (
    ConfirmIn,
    Contract,
    Identifier,
    Money,
    OrderOut,
)

router = APIRouter(prefix="/b2b/api/assistant", tags=["B2B assistant"])


class OpenIn(Contract):
    thread_id: Identifier


class MessageIn(Contract):
    message_id: Identifier
    message: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    customer_id: Identifier | None = None
    read_only: StrictBool = False


class ActionIn(ConfirmIn):
    order_id: Identifier


class CancelIn(Contract):
    operation_id: Identifier
    order_id: Identifier
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation required.")
        return value


class ConversationPage(Contract):
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)


class HistoryPage(Contract):
    limit: int = Field(default=20, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=100)


class CardOut(Money):
    product_id: str
    organization_id: str
    sku: str
    name: str
    quantity: int
    reserved: int
    available: int
    revision: int
    observed_at: str


def public_turn(turn):
    # Whitelist, never serialize graph state, action plans, prompts or classifiers.
    result = {
        k: turn[k]
        for k in ("turn_id", "kind", "message", "status", "created_at", "operation")
    }
    reply = turn["reply"]
    if reply is None:
        result["reply"] = None
        return result
    if not isinstance(reply.get("text"), str) or len(reply["text"]) > 8000:
        raise BffUnavailable("Invalid assistant response.")
    result["reply"] = {
        "intent": reply["intent"],
        "text": reply["text"],
        "products": [checked(CardOut, card) for card in reply.get("products", [])],
        "order": checked(OrderOut, reply["order"]) if reply.get("order") else None,
        "can_confirm": bool(reply.get("can_confirm")),
    }
    return result


def public_history(value):
    return {**value, "items": [public_turn(turn) for turn in value["items"]]}


async def invoke(request, method, *args):
    assistant = getattr(request.app.state, "bff_assistant", None)
    if assistant is None:
        raise BffUnavailable("Assistant unavailable.")
    source, context = request.state.tenant_request, request.state.tenant_context

    def execute():
        assistant.current(source, context)
        value = method(assistant)(source, context, *args)
        # The synchronous PostgreSQL checkpointer never blocks the ASGI event loop.
        return asyncio.run(value) if inspect.isawaitable(value) else value

    return await worker(request, execute)


@router.get("/conversations")
async def conversations(request: Request):
    page = query(request, ConversationPage)
    return {
        "items": await invoke(
            request, lambda a: a.store.conversations_list, page.limit, page.offset
        ),
        **page.model_dump(),
    }


@router.post("/conversations")
async def open_conversation(request: Request, body: OpenIn):
    conversation_id = await invoke(request, lambda a: a.store.open, body.thread_id)
    return {"conversation_id": conversation_id.conversation_id}


@router.get("/conversations/{conversation_id}")
async def history(request: Request, conversation_id: Identifier):
    page = query(request, HistoryPage)
    return public_history(
        await invoke(
            request, lambda a: a.read, conversation_id, page.limit, page.offset
        )
    )


@router.post("/conversations/{conversation_id}/messages")
async def message(request: Request, conversation_id: Identifier, body: MessageIn):
    return public_turn(
        await invoke(
            request,
            lambda a: a.send,
            conversation_id,
            body.message_id,
            body.message,
            body.customer_id,
            body.read_only,
        )
    )


@router.post("/conversations/{conversation_id}/resume")
async def resume(request: Request, conversation_id: Identifier):
    return public_history(await invoke(request, lambda a: a.resume, conversation_id))


@router.post("/conversations/{conversation_id}/turns/{turn_id}/retry")
async def retry(request: Request, conversation_id: Identifier, turn_id: Identifier):
    return public_turn(
        await invoke(request, lambda a: a.retry, conversation_id, turn_id)
    )


@router.post("/conversations/{conversation_id}/confirm")
async def confirm(request: Request, conversation_id: Identifier, body: ActionIn):
    request.state.operation = {
        "namespace": "confirm",
        "operation_id": body.operation_id,
    }
    return public_turn(
        await invoke(
            request,
            lambda a: a.action,
            conversation_id,
            "confirm",
            body.operation_id,
            body.order_id,
            body.preparation_digest,
        )
    )


@router.post("/conversations/{conversation_id}/cancel")
async def cancel(request: Request, conversation_id: Identifier, body: CancelIn):
    request.state.operation = {"namespace": "cancel", "operation_id": body.operation_id}
    return public_turn(
        await invoke(
            request,
            lambda a: a.action,
            conversation_id,
            "cancel",
            body.operation_id,
            body.order_id,
        )
    )


@router.post("/conversations/{conversation_id}/close")
async def close(request: Request, conversation_id: Identifier):
    await invoke(request, lambda a: a.store.close, conversation_id)
    return {"status": "closed"}
