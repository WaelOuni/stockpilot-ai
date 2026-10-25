"""Explicit B2B reads. No optional organization or legacy fallback."""

from typing import Protocol

from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.domain.entities.tenant_stock_operation import ImportMode
from stockpilot.domain.entities.tenant_product import (
    TenantProduct,
    TenantProductPage,
    TenantProductQuery,
    TenantStock,
)


class TenantCatalogueUnavailable(Exception):
    """Storage, schema or persisted fact integrity failure; never a 404/403."""


class TenantCataloguePort(Protocol):
    def export_rows(
        self, request: object, context: TenantContext, mode: ImportMode,
        category: str | None = None,
    ) -> dict[str, list]: ...

    def search(
        self, request: object, context: TenantContext, query: TenantProductQuery
    ) -> TenantProductPage: ...

    def by_sku(
        self, request: object, context: TenantContext, sku: str
    ) -> TenantProduct | None: ...

    def resolution_candidates(
        self,
        request: object,
        context: TenantContext,
        reference: str,
        *,
        exact: bool = False,
        limit: int = 21,
    ) -> tuple[TenantProduct, ...]: ...

    def by_id(
        self, request: object, context: TenantContext, product_id: str
    ) -> TenantProduct | None: ...


class TenantStockPort(Protocol):
    def stock(
        self, request: object, context: TenantContext, product_id: str
    ) -> TenantStock | None: ...

    def available(
        self, request: object, context: TenantContext, product_id: str, quantity: int
    ) -> bool: ...
