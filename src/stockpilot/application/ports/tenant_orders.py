"""Inactive B2B use-case port: mandatory verified server request/context."""

from typing import Protocol

from stockpilot.domain.entities.tenant_authorization import (
    BuyerAssociation,
    TenantContext,
)
from stockpilot.domain.entities.tenant_order import (
    CancelTenantOrder,
    ConfirmTenantOrder,
    PrepareTenantOrder,
    TenantOrder,
)


class TenantOrderConflict(Exception):
    """Idempotency mismatch, stale preparation, stock or state conflict."""


class TenantOrderNotFound(Exception):
    """Missing and foreign orders/associations are deliberately indistinguishable."""


class TenantOrdersUnavailable(Exception):
    """Technical dependency/schema/integrity failure, not a business denial."""


class TenantOrderOutcomeUnknown(TenantOrdersUnavailable):
    """Commit may have succeeded. Retry ONLY the same request and operation key."""


class TenantOrdersPort(Protocol):
    def prepare(
        self, request: object, context: TenantContext, command: PrepareTenantOrder
    ) -> TenantOrder: ...
    def confirm(
        self, request: object, context: TenantContext, command: ConfirmTenantOrder
    ) -> TenantOrder: ...
    def cancel(
        self, request: object, context: TenantContext, command: CancelTenantOrder
    ) -> TenantOrder: ...
    def get(
        self, request: object, context: TenantContext, order_id: str
    ) -> TenantOrder: ...

    def list(
        self, request: object, context: TenantContext, limit: int = 20, offset: int = 0
    ) -> tuple[TenantOrder, ...]: ...

    def buyers(
        self, request: object, context: TenantContext, limit: int = 20, offset: int = 0
    ) -> tuple[BuyerAssociation, ...]: ...

    def result(
        self, request: object, context: TenantContext, namespace: str, operation_id: str
    ) -> TenantOrder: ...
