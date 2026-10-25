"""HTTP-only contracts. No browser field supplies tenant identity or permissions."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
)

Identifier = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$"
    ),
]
Positive = Annotated[StrictInt, Field(ge=1, le=1_000_000)]
Revision = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class LineIn(Contract):
    sku: Identifier
    quantity: Positive


class PrepareIn(Contract):
    operation_id: Identifier
    customer_id: Identifier
    lines: Annotated[list[LineIn], Field(min_length=1, max_length=20)]


class ConfirmIn(Contract):
    operation_id: Identifier
    preparation_digest: Identifier
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean confirmation required.")
        return value


class CancelIn(Contract):
    operation_id: Identifier


class ArchiveIn(Contract):
    operation_id: Identifier
    expected_revision: Revision
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean confirmation required.")
        return value


class AdjustmentIn(Contract):
    operation_id: Identifier
    quantity: Annotated[StrictInt, Field(ge=0, le=1_000_000_000)]
    expected_revision: Revision
    reason: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]


class ApplyIn(Contract):
    operation_id: Identifier
    import_id: Identifier
    preview_digest: Identifier
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean confirmation required.")
        return value


class Money(Contract):
    price_minor: str
    currency: str

    @field_validator("price_minor", mode="before")
    @classmethod
    def exact_price(cls, value):
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise ValueError("Exact persisted minor-unit integer required.")
        return str(value)


class ProductOut(Money):
    product_id: str
    organization_id: str
    sku: str
    name: str
    description: str
    category: str
    status: str
    attributes: tuple[str, ...]
    revision: int


class StockOut(Contract):
    product_id: str
    organization_id: str
    quantity: int
    reserved: int
    available: int
    status: str
    revision: int


class OrderLineOut(Money):
    product_id: str
    sku: str
    quantity: int


class OrderOut(Contract):
    organization_id: str
    order_id: str
    customer_id: str
    created_by: str
    status: Literal["prepared", "reserved", "cancelled"]
    lines: tuple[OrderLineOut, ...]
    operation_id: str
    preparation_digest: str
    created_at: datetime
    expires_at: datetime
    reserved_at: datetime | None
    cancelled_at: datetime | None
    revision: int


class BuyerOut(Contract):
    association_id: str
    organization_id: str
    user_id: str
    customer_id: str
    status: Literal["active"]
    revision: str


class IssueOut(Contract):
    line: int
    code: str
    message: str


class PreviewOut(Contract):
    import_id: str
    organization_id: str
    actor_user_id: str
    mode: Literal["create_only", "metadata_only", "stock_only"]
    status: str
    file_fingerprint: str
    preview_digest: str
    rows: int
    issues: tuple[IssueOut, ...]
    expires_at: datetime


class OperationOut(Contract):
    operation_id: str
    namespace: Literal["adjust", "import", "archive"]
    organization_id: str
    actor_user_id: str
    status: Literal["pending", "succeeded", "rejected"]
    code: str
    movement_ids: tuple[str, ...]
    import_id: str | None


class MovementOut(Contract):
    movement_id: str
    organization_id: str
    product_id: str
    sku: str
    operation_id: str
    actor_user_id: str
    kind: str
    operation_namespace: str
    quantity_before: int
    quantity_after: int
    reserved_before: int
    reserved_after: int
    revision_after: int
    delta: int
    reason: str
    source_reference: str
    occurred_at: datetime
