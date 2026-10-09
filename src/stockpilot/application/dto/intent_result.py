from pydantic import BaseModel, field_validator

from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory


class IntentResult(BaseModel):
    intent: Intent
    raw_product: str | None = None
    product_name: str | None = None
    category: ProductCategory | None = None
    budget: float | None = None
    usage: str | None = None
    quantity: int | None = None

    @field_validator("product_name", "raw_product", "usage", mode="before")
    @classmethod
    def normalize_null_strings(cls, value):
        if isinstance(value, str) and value.strip().lower() in {"null", "none", ""}:
            return None

        return value
