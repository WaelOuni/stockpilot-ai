"""Thin contact routes behind the existing BFF cookie/Origin/CSRF boundary."""
from datetime import datetime
from typing import Annotated, Literal
from fastapi import APIRouter, Request
from pydantic import Field, StrictStr
from stockpilot.application.ports.bff_security import BffUnavailable
from stockpilot.domain.entities.tenant_contact import CreateContact, ReplyContact, SetContactStatus, ContactQuery
from stockpilot.presentation.api.b2b_boundary import worker
from stockpilot.presentation.api.routes.bff_auth import origin
from stockpilot.presentation.api.routes.b2b_business import Page, query, checked
from stockpilot.presentation.api.schemas.b2b import Contract, Identifier

router = APIRouter(prefix="/b2b/api/contact", tags=["B2B contact"])
Category = Literal["access", "catalogue", "order", "stock", "other"]
Body = Annotated[StrictStr, Field(min_length=1, max_length=4000)]


class CreateIn(Contract):
    operation_id: Identifier
    subject: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    category: Category
    description: Body


class ReplyIn(Contract):
    operation_id: Identifier
    body: Body


class StatusIn(Contract):
    operation_id: Identifier
    status: Literal["open", "resolved"]


class Filters(Page):
    scope: Literal["own", "organization"] = "own"
    status: Literal["open", "resolved"] | None = None


class ContactOut(Contract):
    request_id: str
    subject: str
    category: Category
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    status: Literal["open", "resolved"]
    revision: int


class ReplyOut(Contract):
    reply_id: str
    request_id: str
    body: str
    created_by: str
    created_at: datetime


class ReceiptOut(Contract):
    operation_id: str
    request_id: str
    reply_id: str | None


async def invoke(request, method, *args):
    if request.headers.getlist("origin"):
        origin(request)
    contact = getattr(request.app.state.bff_business, "contact", None)
    if contact is None:
        raise BffUnavailable("Contact unavailable.")
    return await worker(request, getattr(contact, method),
                        request.state.tenant_request, request.state.tenant_context, *args)


def public_page(values, model):
    return {**values, "items": [checked(model, value) for value in values["items"]]}


@router.post("/requests")
async def create(request: Request, body: CreateIn):
    return checked(ReceiptOut, await invoke(request, "create", CreateContact(**body.model_dump())))


@router.get("/requests")
async def list_requests(request: Request):
    return public_page(await invoke(request, "list", ContactQuery(**query(request, Filters).model_dump())), ContactOut)


@router.get("/requests/{request_id}")
async def get(request: Request, request_id: Identifier):
    return checked(ContactOut, await invoke(request, "get", request_id))


@router.get("/requests/{request_id}/replies")
async def replies(request: Request, request_id: Identifier):
    page = query(request, Page)
    return public_page(await invoke(request, "replies", request_id, page.limit, page.offset), ReplyOut)


@router.post("/requests/{request_id}/replies")
async def reply(request: Request, request_id: Identifier, body: ReplyIn):
    return checked(ReceiptOut, await invoke(request, "reply", ReplyContact(request_id=request_id, **body.model_dump())))


@router.post("/requests/{request_id}/status")
async def set_status(request: Request, request_id: Identifier, body: StatusIn):
    return checked(ReceiptOut, await invoke(request, "set_status", SetContactStatus(request_id=request_id, **body.model_dump())))
