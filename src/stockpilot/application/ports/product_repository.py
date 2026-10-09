from typing import Protocol

from stockpilot.application.dto.stock_reservation_result import (
    StockReservationResult,
)
from stockpilot.domain.entities.product import Product


class ProductRepositoryPort(Protocol):
    async def find_by_name(self, name: str) -> Product | None:
        ...

    async def reserve_stock(
        self,
        product_name: str,
        quantity: int,
    ) -> StockReservationResult:
        ...

    async def release_stock(
        self,
        product_name: str,
        quantity: int,
    ) -> bool:
        ...