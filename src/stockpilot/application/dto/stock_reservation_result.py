from dataclasses import dataclass


@dataclass(frozen=True)
class StockReservationResult:
    success: bool
    remaining_stock: int | None = None
