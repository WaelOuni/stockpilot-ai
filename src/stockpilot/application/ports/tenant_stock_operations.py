"""Internal-only B2B contracts. Reuse B2's technical/unknown outcome semantics."""

from typing import Protocol

from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.domain.entities.tenant_stock_operation import (
    AdjustTenantStock,
    ApplyTenantImport,
    ImportMode,
    StockOperationResult,
    TenantImportPreview,
    TenantStockMovement,
)


class TenantStockOperationConflict(Exception):
    """Same operation key with different content/actor."""


class TenantStockResourceNotFound(Exception):
    """Absent/foreign preview, product or result; no existence oracle."""


class TenantStockOperationsPort(Protocol):
    def adjust(
        self, request: object, context: TenantContext, command: AdjustTenantStock
    ) -> StockOperationResult: ...
    def preview(
        self, request: object, context: TenantContext, content: bytes, mode: ImportMode
    ) -> TenantImportPreview: ...
    def apply(
        self, request: object, context: TenantContext, command: ApplyTenantImport
    ) -> StockOperationResult: ...
    def result(
        self, request: object, context: TenantContext, namespace: str, operation_id: str
    ) -> StockOperationResult: ...
    def movements(
        self, request: object, context: TenantContext, limit: int = 20, offset: int = 0
    ) -> tuple[TenantStockMovement, ...]: ...
    def imports(
        self, request: object, context: TenantContext, limit: int = 20, offset: int = 0
    ) -> tuple[TenantImportPreview, ...]: ...
