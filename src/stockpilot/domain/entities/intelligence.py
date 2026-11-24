"""Analytics query contract, deliberately independent of all ORDER commands."""
from dataclasses import dataclass


@dataclass(frozen=True)
class IntelligenceQuery:
    period_days: int = 90
    horizon_days: int = 30
    low_stock_threshold: int = 5
    category: str | None = None
    product_id: str | None = None
    status: str = "active"
    limit: int = 20
    offset: int = 0
    discount_bps: int = 1000

    def __post_init__(self):
        if type(self.period_days) is not int or self.period_days not in (30, 90, 180, 365):
            raise ValueError("Explicit analysis period required.")
        if type(self.horizon_days) is not int or self.horizon_days not in (7, 30, 60, 90):
            raise ValueError("Supported forecast horizon required.")
        for value, low, high in ((self.limit, 1, 20), (self.offset, 0, 10000), (self.low_stock_threshold, 0, 1000000), (self.discount_bps, 0, 9000)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Bounded integer filters required.")
        if self.status not in ("active", "inactive"):
            raise ValueError("Known product status required.")
        for value in (self.category, self.product_id):
            if value is not None and (type(value) is not str or not 1 <= len(value) <= 128 or value != value.strip()):
                raise ValueError("Exact scalar filter required.")
