"""Trusted server ports. No global lookup, fallback, provisioning or writes."""

from typing import Protocol

from stockpilot.domain.entities.tenant_authorization import (
    BuyerAssociation,
    ResourceOwnership,
    ResourceReference,
    TenantContext,
)


class TenantContextUnavailable(Exception):
    """Authentication/identity infrastructure failed; never an identity absence."""


class ResourceOwnershipUnavailable(Exception):
    """Technical or integrity failure of scoped ownership storage."""


class BuyerAssociationUnavailable(Exception):
    """Technical or integrity failure of explicit buyer-grant storage."""


class TenantContextSource(Protocol):
    def current(self, request_context: object) -> TenantContext | None:
        """Reauthenticate and freshly resolve active user/org/membership/permissions.

        Return None for invalid/expired credentials or inactive access; raise
        TenantContextUnavailable for technical failures. No stale Principal cache.
        The context ID identifies a validated server session, not a browser flag.
        """
        ...


class ResourceOwnershipRepository(Protocol):
    def find(
        self, organization_id: str, reference: ResourceReference
    ) -> tuple[ResourceOwnership, ...]:
        """Load authoritative ownership using BOTH org and exact kind/ID.

        Return zero or one record; never globally look up then infer an org.
        Missing/unattributed/foreign records are indistinguishable. Future adapters
        must detect duplicates, return an immutable revision, and raise
        ResourceOwnershipUnavailable on technical failure. No implementation yet.
        """
        ...


class BuyerAssociationRepository(Protocol):
    def find(
        self, organization_id: str, user_id: str, customer_id: str
    ) -> tuple[BuyerAssociation, ...]:
        """Exact explicitly provisioned association, including disabled records.

        One current record per (org,user,customer); return all candidates to reject
        ambiguity. Never infer an association from email, JWT or ID equality.
        Raise BuyerAssociationUnavailable on technical failure.
        """
        ...
