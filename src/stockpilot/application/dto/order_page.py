from dataclasses import dataclass
from stockpilot.domain.entities.order import Order

@dataclass(frozen=True)
class OrderPage:
    items: list[Order]
    total: int
    has_more: bool
