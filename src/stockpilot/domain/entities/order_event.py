from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from stockpilot.domain.enums.order_status import OrderStatus


class OrderEventSource(str, Enum):
    SYSTEM = "SYSTEM"
    BACKOFFICE = "BACKOFFICE"
    CUSTOMER = "CUSTOMER"


@dataclass(frozen=True)
class OrderEvent:
    status: OrderStatus
    occurred_at: datetime
    source: OrderEventSource
