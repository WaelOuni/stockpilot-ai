"""Read-only analytics; organization identity comes exclusively from the BFF."""
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import Field

from stockpilot.application.ports.tenant_catalogue import TenantCatalogueUnavailable
from stockpilot.domain.entities.intelligence import IntelligenceQuery
from stockpilot.presentation.api.b2b_boundary import worker
from stockpilot.presentation.api.routes.b2b_business import query
from stockpilot.presentation.api.routes.bff_auth import origin
from stockpilot.presentation.api.schemas.b2b import Contract

router = APIRouter(prefix="/b2b/api/intelligence", tags=["B2B Intelligence"])


class Filters(Contract):
    period_days: int = Field(default=90, ge=30, le=365)
    horizon_days: int = Field(default=30, ge=7, le=90)
    low_stock_threshold: int = Field(default=5, ge=0, le=1000000)
    category: str | None = Field(default=None, min_length=1, max_length=128)
    product_id: str | None = Field(default=None, pattern="^[0-9a-f]{24}$")
    status: Literal["active", "inactive"] = "active"
    limit: int = Field(default=20, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=10000)
    discount_bps: int = Field(default=1000, ge=0, le=9000)


@router.get("")
async def dashboard(request: Request):
    if request.headers.getlist("origin"):
        origin(request)
    service = getattr(request.app.state.bff_business, "intelligence", None)
    if service is None:
        raise TenantCatalogueUnavailable("Intelligence unavailable.")
    filters = IntelligenceQuery(**query(request, Filters).model_dump())
    return await worker(request, service.dashboard, request.state.tenant_request,
                        request.state.tenant_context, filters)
