from dataclasses import dataclass

from stockpilot.domain.enums.product_category import ProductCategory


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: ProductCategory
    price: float
    stock: int

    @property
    def available(self) -> bool:
        return self.stock > 0