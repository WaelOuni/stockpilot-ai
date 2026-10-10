from typing import TypedDict

from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory


class StockPilotState(TypedDict, total=False):
    message: str
    intent: Intent
    raw_product: str | None
    product_name: str | None
    category: ProductCategory | None
    budget: float | None
    usage: str | None
    quantity: int | None
    pending_order: dict[str, str | int] | None
    order_status: str
    confirmation_handled: bool
    created_order_id: str | None
    last_created_order_id: str
    order_id: str | None
    queried_order_id: str | None
    queried_order_status: str | None
    order_status_result: str
    response: str
