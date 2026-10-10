from typing import Protocol

from stockpilot.application.dto.product_page import ProductPage
from stockpilot.application.dto.stock_reservation_result import (
    StockReservationResult,
)
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


class ProductRepositoryPort(Protocol):
    async def list_products(
        self, limit: int, offset: int, *, q: str | None = None,
        category: ProductCategory | None = None, available: bool | None = None,
    ) -> ProductPage:
        ...

    async def find_by_sku(self, sku: str) -> Product | None:
        ...

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