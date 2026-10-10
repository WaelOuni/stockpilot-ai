from datetime import datetime

from pydantic import BaseModel, ConfigDict

from stockpilot.domain.enums.order_status import OrderStatus


class UpdateOrderStatusRequest(BaseModel):
    status: OrderStatus


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    product_name: str
    quantity: int
    status: OrderStatus
    created_at: datetime
