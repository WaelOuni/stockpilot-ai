from datetime import datetime, timezone
from uuid import UUID, uuid4

from stockpilot.application.ports.product_repository import ProductRepositoryPort
from stockpilot.application.dto.stock_movement_page import StockMovementPage
from stockpilot.domain.entities.stock_movement import StockMovement


MAX_STOCK_DELTA = 1_000_000
MAX_STOCK_REASON_LENGTH = 500


class StockAdjustmentConflictError(Exception):
    pass


class InsufficientStockError(Exception):
    pass


class ProductStockNotFoundError(Exception):
    pass


class StockMovementIndexNotReadyError(Exception):
    pass


class StockAdjustmentOutcomeUnknownError(Exception):
    pass


class StockAdjustmentTransactionFailedError(Exception):
    pass


class StockAdjustmentAmbiguousProductError(Exception):
    pass


def validate_stock_adjustment(sku: str, delta: int, reason: str, operation_id: str) -> str:
    if not isinstance(sku, str) or not sku.strip():
        raise ValueError("Product SKU is required.")
    if type(delta) is not int or delta == 0 or abs(delta) > MAX_STOCK_DELTA:
        raise ValueError("Stock delta must be a non-zero bounded integer.")
    if not isinstance(reason, str):
        raise ValueError("Stock adjustment reason is required.")
    reason = reason.strip()
    if not reason or len(reason) > MAX_STOCK_REASON_LENGTH:
        raise ValueError("Stock adjustment reason is invalid.")
    if not isinstance(operation_id, str):
        raise ValueError("Operation ID must be a UUID.")
    try:
        parsed = UUID(operation_id)
        if str(parsed) != operation_id or parsed.version != 4:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("Operation ID must be a canonical UUID.") from None
    return reason


class AdjustProductStockUseCase:
    def __init__(self, repository: ProductRepositoryPort):
        self.repository = repository

    async def execute(
        self, sku: str, delta: int, reason: str, operation_id: str,
    ) -> StockMovement:
        normalized_reason = validate_stock_adjustment(sku, delta, reason, operation_id)
        occurred_at = datetime.now(timezone.utc)
        occurred_at = occurred_at.replace(microsecond=(occurred_at.microsecond // 1000) * 1000)
        return await self.repository.adjust_stock(
            sku=sku,
            delta=delta,
            reason=normalized_reason,
            operation_id=operation_id,
            movement_id=str(uuid4()),
            occurred_at=occurred_at,
        )


class GetStockAdjustmentUseCase:
    def __init__(self, repository: ProductRepositoryPort):
        self.repository = repository

    async def execute(self, operation_id: str) -> StockMovement | None:
        if not isinstance(operation_id, str):
            raise ValueError("Operation ID must be a UUID.")
        try:
            parsed = UUID(operation_id)
            if str(parsed) != operation_id or parsed.version != 4:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError("Operation ID must be a canonical UUID.") from None
        return await self.repository.find_stock_adjustment(operation_id)


class ListStockMovementsUseCase:
    def __init__(self, repository: ProductRepositoryPort):
        self.repository = repository

    async def execute(self, sku: str, limit: int, offset: int, *, include_total: bool = False) -> StockMovementPage:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("Invalid stock history pagination.")
        try:
            product = await self.repository.find_by_sku(sku)
        except ValueError as error:
            raise StockAdjustmentAmbiguousProductError("Product SKU is ambiguous.") from error
        if product is None:
            raise ProductStockNotFoundError("Product not found.")
        if include_total:
            return await self.repository.list_stock_movements(sku, limit, offset, include_total=True)
        return await self.repository.list_stock_movements(sku, limit, offset)
