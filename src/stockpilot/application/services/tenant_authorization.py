"""Read-only preflight boundary. ALLOW is not a mutation capability."""

from dataclasses import dataclass
from types import MappingProxyType

from stockpilot.application.ports.tenant_authorization import (
    BuyerAssociationRepository,
    BuyerAssociationUnavailable,
    ResourceOwnershipRepository,
    ResourceOwnershipUnavailable,
    TenantContextSource,
    TenantContextUnavailable,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_authorization import (
    BuyerAssociation,
    OrderResource,
    OrganizationResource,
    ResourceReference,
    TenantContext,
)
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.domain.enums.tenant_authorization import (
    AuthorizationDecision as D,
)
from stockpilot.domain.enums.tenant_authorization import (
    BuyerAssociationStatus,
)
from stockpilot.domain.enums.tenant_authorization import (
    ResourceKind as K,
)
from stockpilot.domain.enums.tenant_authorization import (
    TenantOperation as O,
)
from stockpilot.domain.services.authorization import ROLE_PERMISSIONS

# An explicit operation also fixes the permitted resource type. No generic
# permission grants access to a different family (e.g. catalogue -> RAG).
_RULES = MappingProxyType(
    {
        O.CATALOGUE_READ: (P.CATALOGUE_READ, K.CATALOGUE_ITEM),
        O.STOCK_READ: (P.STOCK_READ, K.STOCK_ITEM),
        O.STOCK_ADJUST: (P.STOCK_ADJUST, K.STOCK_ITEM),
        O.ORDER_READ_OWN: (P.ORDER_READ_OWN, K.ORDER),
        O.ORDER_READ_ORGANIZATION: (P.ORDER_READ_ORGANIZATION, K.ORDER),
        O.ORDER_CANCEL_OWN: (P.ORDER_CANCEL_OWN, K.ORDER),
        O.ORDER_CANCEL_ORGANIZATION: (P.ORDER_CANCEL_ORGANIZATION, K.ORDER),
        O.IMPORT_EXECUTE: (P.IMPORT_EXECUTE, K.IMPORT_JOB),
        O.MANAGE_MEMBERS: (P.ORGANIZATION_MANAGE_MEMBERS, K.ORGANIZATION_MEMBERSHIP),
    }
)
_OWN = frozenset({O.ORDER_READ_OWN, O.ORDER_CANCEL_OWN})


@dataclass(frozen=True)
class TenantAuthorizationService:
    contexts: TenantContextSource
    ownership: ResourceOwnershipRepository
    buyers: BuyerAssociationRepository

    def resolve_context(self, request_context: object) -> TenantContext:
        """Trusted server composition only; never deserialize a Principal here."""
        if self.contexts is None:
            raise TenantContextUnavailable("Tenant context source unavailable.")
        context = self.contexts.current(request_context)
        if not isinstance(context, TenantContext):
            raise AuthorizationDenied("Current tenant context required.")
        if context.principal.permissions != ROLE_PERMISSIONS.get(
            context.principal.role
        ):
            raise AuthorizationDenied("Inconsistent effective permissions.")
        return context

    def decide(
        self,
        request_context: object,
        context: TenantContext | None,
        operation: O,
        reference: ResourceReference,
    ) -> D:
        if (
            not isinstance(context, TenantContext)
            or not isinstance(operation, O)
            or not isinstance(reference, ResourceReference)
        ):
            return D.DENY
        try:
            # A supplied frozen snapshot is still untrusted until revalidated.
            current = self.resolve_context(request_context)
            if current != context:
                return D.DENY
            rule = _RULES.get(operation)
            if rule is None:
                return D.DENY  # Reserved operations, including create/replay.
            permission, kind = rule
            if (
                permission not in current.principal.permissions
                or reference.kind is not kind
            ):
                return D.DENY
            if self.ownership is None:
                return D.INDETERMINATE
            rows = self.ownership.find(current.organization_id, reference)
            if not isinstance(rows, tuple) or len(rows) > 1:
                return D.INDETERMINATE
            if not rows:
                return D.NOT_FOUND
            row = rows[0]
            expected = OrderResource if kind is K.ORDER else OrganizationResource
            if not isinstance(row, expected) or row.reference != reference:
                return D.INDETERMINATE
            if row.organization_id != current.organization_id:
                return D.NOT_FOUND
            if isinstance(row, OrderResource):
                return self._order(current, operation, row)
            return D.ALLOW
        except AuthorizationDenied:
            return D.DENY
        except (
            TenantContextUnavailable,
            ResourceOwnershipUnavailable,
            BuyerAssociationUnavailable,
        ):
            return D.INDETERMINATE

    def _order(self, context: TenantContext, operation: O, row: OrderResource) -> D:
        if row.owner_user_id is None or row.customer_id is None:
            return D.NOT_FOUND
        if operation in _OWN and row.owner_user_id != context.principal.user_id:
            return D.NOT_FOUND
        if self.buyers is None:
            return D.INDETERMINATE
        # Even organization-wide operations use the persisted owner's exact
        # association, never a customer/user supplied by the requesting browser.
        rows = self.buyers.find(row.organization_id, row.owner_user_id, row.customer_id)
        if not isinstance(rows, tuple) or len(rows) > 1:
            return D.INDETERMINATE
        if not rows:
            return D.NOT_FOUND
        association = rows[0]
        if not isinstance(association, BuyerAssociation) or (
            association.organization_id,
            association.user_id,
            association.customer_id,
        ) != (row.organization_id, row.owner_user_id, row.customer_id):
            return D.INDETERMINATE
        if association.status is not BuyerAssociationStatus.ACTIVE:
            return D.NOT_FOUND
        return D.ALLOW
