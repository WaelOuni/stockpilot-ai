"""Internal B2B ports. No public route composes these adapters."""

from typing import Protocol

from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.domain.entities.tenant_knowledge import IngestTenantDocument


class TenantDataUnavailable(RuntimeError):
    """Required storage/identity/embedding dependency unavailable."""


class TenantDataOutcomeUnknown(TenantDataUnavailable):
    """Retry the same operation; timeout never proves rollback."""


class TenantDataNotFound(LookupError):
    """Absent and foreign resources intentionally indistinguishable."""


class TenantDataConflict(RuntimeError):
    """Revision or idempotency conflict; no implicit overwrite."""


class TenantKnowledgePort(Protocol):
    async def ingest(
        self, request: object, context: TenantContext, command: IngestTenantDocument
    ): ...
    def document(self, request: object, context: TenantContext, document_id: str): ...
    def search(
        self,
        request: object,
        context: TenantContext,
        embedding,
        *,
        model: str,
        version: str,
        limit: int = 3,
    ): ...
    def delete(
        self,
        request: object,
        context: TenantContext,
        document_id: str,
        *,
        operation_id: str,
        expected_revision: int,
    ): ...
