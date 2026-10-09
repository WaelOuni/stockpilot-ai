from stockpilot.application.dto.stock_reservation_result import (
    StockReservationResult,
)
from stockpilot.application.ports.product_repository import ProductRepositoryPort


class ReserveStockUseCase:
    def __init__(self, product_repository: ProductRepositoryPort) -> None:
        self._product_repository = product_repository

    async def execute(
        self,
        product_name: str,
        quantity: int,
    ) -> StockReservationResult:
        normalized_name = product_name.strip()
        if not normalized_name:
            raise ValueError("Le nom du produit est obligatoire.")
        if quantity < 1:
            raise ValueError("La quantité doit être au moins égale à 1.")

        return await self._product_repository.reserve_stock(
            product_name=normalized_name,
            quantity=quantity,
        )
