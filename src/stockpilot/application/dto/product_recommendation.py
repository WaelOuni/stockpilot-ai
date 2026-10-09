from pydantic import BaseModel, field_validator


class ProductRecommendation(BaseModel):
    product_name: str | None
    answer: str

    @field_validator("product_name", mode="before")
    @classmethod
    def normalize_null_product_name(cls, value):
        if isinstance(value, str) and value.strip().lower() in {"null", "none", ""}:
            return None

        return value
