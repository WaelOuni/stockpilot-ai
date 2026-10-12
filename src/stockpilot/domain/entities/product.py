from dataclasses import dataclass, field

from stockpilot.domain.enums.product_category import ProductCategory


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: ProductCategory
    price: float
    stock: int
    document_id: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return self.stock > 0