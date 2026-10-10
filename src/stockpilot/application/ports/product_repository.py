from typing import Protocol
from datetime import datetime

from stockpilot.application.dto.product_page import ProductPage
from stockpilot.application.dto.stock_movement_page import StockMovementPage
from stockpilot.application.dto.stock_reservation_result import (
    StockReservationResult,
)
from stockpilot.domain.entities.product import Product
from stockpilot.domain.entities.stock_movement import StockMovement
from stockpilot.domain.enums.product_category import ProductCategory


class ProductRepositoryPort(Protocol):
    async def create_product(self, product: Product) -> Product:
        ...

    async def update_metadata(self, sku: str, changes: dict) -> Product | None:
        ...

    async def list_products(
        self, limit: int, offset: int, *, q: str | None = None,
        category: ProductCategory | None = None, available: bool | None = None,
    ) -> ProductPage:
        ...

    async def find_by_sku(self, sku: str) -> Product | None:
        ...

    async def adjust_stock(
        self, *, sku: str, delta: int, reason: str, operation_id: str,
        movement_id: str, occurred_at: datetime,
    ) -> StockMovement:
        ...

    async def find_stock_adjustment(self, operation_id: str) -> StockMovement | None:
        ...

    async def list_stock_movements(
        self, sku: str, limit: int, offset: int,
    ) -> StockMovementPage:
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