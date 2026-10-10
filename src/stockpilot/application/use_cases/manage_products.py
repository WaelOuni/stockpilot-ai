import math

from stockpilot.application.ports.product_repository import ProductRepositoryPort
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


class DuplicateProductSkuError(Exception):
    pass


class ProductSkuIndexNotReadyError(Exception):
    pass


def validate_metadata(values: dict) -> None:
    if not values or set(values) - {"name", "category", "price"}:
        raise ValueError("Only product metadata may be changed.")
    if "name" in values and (not isinstance(values["name"], str) or not values["name"].strip()):
        raise ValueError("Product name must not be blank.")
    if "category" in values and not isinstance(values["category"], ProductCategory):
        raise ValueError("Invalid product category.")
    if "price" in values:
        price = values["price"]
        if type(price) not in (int, float) or not math.isfinite(price) or price < 0:
            raise ValueError("Price must be finite and nonnegative.")


class CreateProductUseCase:
    def __init__(self, repository: ProductRepositoryPort):
        self.repository = repository

    async def execute(self, sku: str, name: str, category: ProductCategory, price: float) -> Product:
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("SKU must not be blank.")
        validate_metadata({"name": name, "category": category, "price": price})
        return await self.repository.create_product(Product(
            sku=sku, name=name, category=category, price=price, stock=0,
        ))


class UpdateProductMetadataUseCase:
    def __init__(self, repository: ProductRepositoryPort):
        self.repository = repository

    async def execute(self, sku: str, changes: dict) -> Product | None:
        validate_metadata(changes)
        return await self.repository.update_metadata(sku, changes)
