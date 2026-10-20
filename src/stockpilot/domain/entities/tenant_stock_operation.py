"""B2B physical stock and CSV contracts; browser data is never authority."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from stockpilot.domain.entities.tenant_order import identifier


class ImportMode(StrEnum):
    CREATE_ONLY = "create_only"
    METADATA_ONLY = "metadata_only"
    STOCK_ONLY = "stock_only"


@dataclass(frozen=True)
class ImportIssue:
    line: int
    code: str
    message: str


class OperationStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"


def quantity(value):
    if type(value) is not int or not 0 <= value <= 1_000_000_000:
        raise ValueError("Bounded nonnegative physical integer quantity required.")


@dataclass(frozen=True)
class AdjustTenantStock:
    operation_id: str
    product_id: str
    quantity: int
    expected_revision: int
    reason: str

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.product_id)
        quantity(self.quantity)
        if type(self.expected_revision) is not int or self.expected_revision < 1:
            raise ValueError("Positive revision required.")
        if (
            type(self.reason) is not str
            or not self.reason.strip()
            or len(self.reason) > 500
            or any(ord(c) < 32 for c in self.reason)
        ):
            raise ValueError("Bounded printable business reason required.")


@dataclass(frozen=True)
class ApplyTenantImport:
    operation_id: str
    import_id: str
    preview_digest: str
    confirmed: bool

    def __post_init__(self):
        for value in (self.operation_id, self.import_id, self.preview_digest):
            identifier(value)
        if self.confirmed is not True:
            raise ValueError("Explicit import confirmation required.")


@dataclass(frozen=True)
class TenantImportPreview:
    import_id: str
    organization_id: str
    actor_user_id: str
    mode: ImportMode
    status: str
    file_fingerprint: str
    preview_digest: str
    rows: int
    issues: tuple[ImportIssue, ...]
    expires_at: datetime


@dataclass(frozen=True)
class StockOperationResult:
    operation_id: str
    namespace: str
    organization_id: str
    actor_user_id: str
    status: OperationStatus
    code: str
    movement_ids: tuple[str, ...]
    import_id: str | None

    def __bool__(self):
        raise TypeError(
            "Compare operation.status explicitly; pending/rejected are not success."
        )


@dataclass(frozen=True)
class TenantStockMovement:
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
    reason: str
    source_reference: str
    occurred_at: datetime

    @property
    def delta(self):
        return self.quantity_after - self.quantity_before
