from pydantic import BaseModel

from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


class ProductResponse(BaseModel):
    sku: str
    product_name: str
    category: ProductCategory
    price: float
    stock: int

    @classmethod
    def from_product(cls, product: Product) -> "ProductResponse":
        return cls(
            sku=product.sku, product_name=product.name, category=product.category,
            price=product.price, stock=product.stock,
        )


class ProductsResponse(BaseModel):
    items: list[ProductResponse]
    limit: int
    offset: int
    count: int
    has_more: bool
