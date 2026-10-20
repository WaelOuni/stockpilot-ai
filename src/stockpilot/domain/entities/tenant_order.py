"""B2B order requests and immutable snapshots; no framework or token payload."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from stockpilot.domain.identity_validation import require_identifier


def identifier(value: str) -> str:
    require_identifier(value)
    if len(value) > 128:
        raise ValueError("Identifier too long.")
    return value


class TenantOrderStatus(StrEnum):
    PREPARED = "prepared"
    RESERVED = "reserved"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RequestedLine:
    sku: str
    quantity: int

    def __post_init__(self):
        identifier(self.sku)
        if type(self.quantity) is not int or not 1 <= self.quantity <= 1_000_000:
            raise ValueError("Strict positive bounded integer quantity required.")


@dataclass(frozen=True)
class PrepareTenantOrder:
    operation_id: str
    customer_id: str
    lines: tuple[RequestedLine, ...]

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.customer_id)
        if not isinstance(self.lines, tuple) or not 1 <= len(self.lines) <= 20:
            raise ValueError("One to twenty typed lines required.")
        if any(not isinstance(line, RequestedLine) for line in self.lines):
            raise TypeError("Typed lines required.")
        if len({line.sku for line in self.lines}) != len(self.lines):
            raise ValueError("Duplicate SKU refused.")


@dataclass(frozen=True)
class ConfirmTenantOrder:
    operation_id: str
    order_id: str
    preparation_digest: str
    confirmed: bool

    def __post_init__(self):
        for value in (self.operation_id, self.order_id, self.preparation_digest):
            identifier(value)
        if self.confirmed is not True:
            raise ValueError("Explicit confirmation required.")


@dataclass(frozen=True)
class CancelTenantOrder:
    operation_id: str
    order_id: str

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.order_id)


@dataclass(frozen=True)
class TenantOrderLine:
    product_id: str
    sku: str
    quantity: int
    price_minor: int
    currency: str


@dataclass(frozen=True)
class TenantOrder:
    organization_id: str
    order_id: str
    customer_id: str
    created_by: str
    status: TenantOrderStatus
    lines: tuple[TenantOrderLine, ...]
    operation_id: str
    preparation_digest: str
    created_at: datetime
    expires_at: datetime
    reserved_at: datetime | None
    cancelled_at: datetime | None
    revision: int
