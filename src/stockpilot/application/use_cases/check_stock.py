from dataclasses import dataclass, field
from stockpilot.telemetry import traced
from stockpilot.metrics import record

from stockpilot.application.ports.product_repository import ProductRepositoryPort


@dataclass(frozen=True)
class StockResult:
    sku: str
    name: str
    stock: int
    available: bool
    document_id: str | None = field(default=None, repr=False)


class CheckStockUseCase:
    def __init__(self, product_repository: ProductRepositoryPort):
        self.product_repository = product_repository

    @traced("stockpilot.stock.check")
    async def execute(self, product_name: str) -> StockResult | None:
        product = await self.product_repository.find_by_name(product_name)

        if product is None:
            record("stock.checks", outcome="not_found")
            return None
        record("stock.checks", outcome="available" if product.available else "unavailable")

        return StockResult(
            sku=product.sku,
            name=product.name,
            stock=product.stock,
            available=product.available,
            document_id=product.document_id,
        )