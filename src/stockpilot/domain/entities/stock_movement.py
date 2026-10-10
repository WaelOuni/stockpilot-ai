from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class StockMovement:
    movement_id: str
    operation_id: str
    product_sku: str
    delta: int
    stock_before: int
    stock_after: int
    reason: str
    occurred_at: datetime
    source: Literal["BACKOFFICE"] = "BACKOFFICE"
