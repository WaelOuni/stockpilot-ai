"""Validated catalogue advice constraints, independent of the LLM."""
import math
from dataclasses import dataclass

from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


@dataclass(frozen=True)
class AdviceCriteria:
    category: ProductCategory | None = None
    minimum: float | None = None
    maximum: float | None = None
    exclusive_maximum: bool = False
    available: bool | None = None
    limit: int = 3
    category_explicit: bool = False
    mode: str = "search"
    reference: str | None = None
    exclude_skus: tuple[str, ...] = ()

    def __post_init__(self):
        # Checkpoints may deserialize string enums as plain strings.
        if self.category is not None:
            object.__setattr__(self, 'category', ProductCategory(self.category))
        if type(self.limit) is not int or not 1 <= self.limit <= 5 or len(self.exclude_skus) > 5:
            raise ValueError('Invalid advice cardinality')
        if self.mode not in {'search', 'product', 'compare', 'cheapest'}:
            raise ValueError('Invalid advice mode')

    def matches(self, product: Product) -> bool:
        return (isinstance(product.sku, str) and bool(product.sku.strip())
                and isinstance(product.name, str) and bool(product.name.strip())
                and isinstance(product.category, ProductCategory)
                and not isinstance(product.price, bool) and isinstance(product.price, (int, float))
                and math.isfinite(product.price) and product.price >= 0
                and not isinstance(product.stock, bool) and isinstance(product.stock, int) and product.stock >= 0
                and product.sku not in self.exclude_skus
                and (self.category is None or product.category == self.category)
                and (self.minimum is None or product.price >= self.minimum)
                and (self.maximum is None or (product.price < self.maximum if self.exclusive_maximum
                                             else product.price <= self.maximum))
                and (self.available is None or product.available == self.available))
