from pydantic import BaseModel

from stockpilot.presentation.api.schemas.order import OrderResponse


class BackofficeOrderResponse(OrderResponse):
    customer_id: str | None = None


class BackofficeOrdersResponse(BaseModel):
    items: list[BackofficeOrderResponse]
    limit: int
    offset: int
    count: int
    total: int | None = None
    has_more: bool | None = None
