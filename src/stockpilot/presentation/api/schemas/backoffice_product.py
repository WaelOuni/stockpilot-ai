from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from stockpilot.domain.enums.product_category import ProductCategory


Nonblank = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
Price = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


class ProductMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Nonblank | None = None
    category: ProductCategory | None = None
    price: Price | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one metadata field is required.")
        for field in self.model_fields_set:
            value = getattr(self, field)
            if value is None or (field == "name" and not value.strip()):
                raise ValueError("Metadata fields must not be null or blank.")
        return self


class CreateProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: Nonblank
    name: Nonblank
    category: ProductCategory
    price: Price

    @model_validator(mode="after")
    def validate_names(self):
        if not self.sku.strip() or not self.name.strip():
            raise ValueError("SKU and name must not be blank.")
        return self
