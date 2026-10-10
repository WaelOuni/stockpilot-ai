from dataclasses import dataclass
from datetime import datetime

from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.entities.order_event import OrderEvent


@dataclass(frozen=True)
class Order:
    order_id: str
    operation_id: str
    product_name: str
    quantity: int
    status: OrderStatus
    created_at: datetime
    customer_id: str | None = None
    events: tuple[OrderEvent, ...] = ()
