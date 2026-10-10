from dataclasses import dataclass

from stockpilot.domain.entities.stock_movement import StockMovement


@dataclass(frozen=True)
class StockMovementPage:
    items: list[StockMovement]
    has_more: bool
