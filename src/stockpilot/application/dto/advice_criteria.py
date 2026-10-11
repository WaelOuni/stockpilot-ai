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

    def matches(self, product: Product) -> bool:
        return (math.isfinite(product.price) and product.price >= 0
                and (self.category is None or product.category == self.category)
                and (self.minimum is None or product.price >= self.minimum)
                and (self.maximum is None or (product.price < self.maximum if self.exclusive_maximum
                                             else product.price <= self.maximum))
                and (self.available is None or product.available == self.available))
