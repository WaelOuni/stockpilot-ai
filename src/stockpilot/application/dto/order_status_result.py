from dataclasses import dataclass
from typing import Literal

from stockpilot.domain.enums.order_status import OrderStatus


@dataclass(frozen=True)
class OrderStatusResult:
    order_id: str | None
    status: OrderStatus | None
    result: Literal["FOUND", "NOT_FOUND", "NO_ORDER_ASSOCIATED"]
    response: str
