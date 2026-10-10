from dataclasses import dataclass

from stockpilot.domain.entities.product import Product


@dataclass(frozen=True)
class ProductPage:
    items: list[Product]
    has_more: bool
