from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.entities.order_event import OrderEventSource


class OrderEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: OrderStatus
    occurred_at: datetime
    source: OrderEventSource


class UpdateOrderStatusRequest(BaseModel):
    status: OrderStatus


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    product_name: str
    quantity: int
    status: OrderStatus
    created_at: datetime
    events: list[OrderEventResponse] = Field(default_factory=list)


class CustomerOrdersResponse(BaseModel):
    items: list[OrderResponse]
    limit: int
    offset: int
    count: int
