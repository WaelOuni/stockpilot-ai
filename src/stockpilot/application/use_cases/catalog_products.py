from stockpilot.application.ports.product_repository import ProductRepositoryPort
from stockpilot.domain.entities.product import Product


class ListProductsUseCase:
    def __init__(self, repository: ProductRepositoryPort) -> None:
        self.repository = repository

    async def execute(self, limit: int = 20, offset: int = 0) -> list[Product]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be nonnegative.")
        return await self.repository.list_products(limit, offset)


class GetProductUseCase:
    def __init__(self, repository: ProductRepositoryPort) -> None:
        self.repository = repository

    async def execute(self, sku: str) -> Product | None:
        return await self.repository.find_by_sku(sku)
