from stockpilot.application.dto.product_page import ProductPage
from stockpilot.application.ports.product_repository import ProductRepositoryPort
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


class ListProductsUseCase:
    def __init__(self, repository: ProductRepositoryPort) -> None:
        self.repository = repository

    async def execute(
        self, limit: int = 20, offset: int = 0, *, q: str | None = None,
        category: ProductCategory | None = None, available: bool | None = None,
        include_total: bool = False,
    ) -> ProductPage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be nonnegative.")
        if q is not None and (not isinstance(q, str) or len(q) > 100):
            raise ValueError("q must be a string of at most 100 characters.")
        if category is not None and not isinstance(category, ProductCategory):
            raise ValueError("category must be a ProductCategory.")
        if available is not None and type(available) is not bool:
            raise ValueError("available must be a boolean.")
        q = (q.strip() or None) if q is not None else None
        if include_total:
            return await self.repository.list_products(limit, offset, q=q, category=category, available=available, include_total=True)
        if q is None and category is None and available is None:
            return await self.repository.list_products(limit, offset)
        return await self.repository.list_products(limit, offset, q=q, category=category, available=available)


class GetProductUseCase:
    def __init__(self, repository: ProductRepositoryPort) -> None:
        self.repository = repository

    async def execute(self, sku: str) -> Product | None:
        return await self.repository.find_by_sku(sku)
