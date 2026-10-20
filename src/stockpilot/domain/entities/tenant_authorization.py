"""Tenant contracts are data, never bearer capabilities or mutation tickets."""

from dataclasses import dataclass, field

from stockpilot.domain.entities.principal import Principal
from stockpilot.domain.enums.tenant_authorization import (
    BuyerAssociationStatus,
    ResourceKind,
    TenantOperation,
)
from stockpilot.domain.identity_validation import require_identifier


@dataclass(frozen=True)
class TenantContext:
    principal: Principal
    authorization_context_id: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.principal, Principal):
            raise TypeError("Resolved human principal required.")
        require_identifier(self.authorization_context_id)

    @property
    def organization_id(self) -> str:
        return self.principal.organization_id


@dataclass(frozen=True)
class ResourceReference:
    kind: ResourceKind
    resource_id: str

    def __post_init__(self):
        if not isinstance(self.kind, ResourceKind):
            raise TypeError("Known resource kind required.")
        require_identifier(self.resource_id)


@dataclass(frozen=True)
class ResourceOwnership:
    reference: ResourceReference
    organization_id: str
    revision: str

    def __post_init__(self):
        if not isinstance(self.reference, ResourceReference):
            raise TypeError("Exact resource reference required.")
        require_identifier(self.organization_id)
        require_identifier(self.revision)


@dataclass(frozen=True)
class OrganizationResource(ResourceOwnership):
    """Organisation-owned catalogue/stock/import/document/admin resources."""

    def __post_init__(self):
        super().__post_init__()
        if self.reference.kind not in {
            ResourceKind.CATALOGUE_ITEM,
            ResourceKind.STOCK_ITEM,
            ResourceKind.IMPORT_JOB,
            ResourceKind.RAG_DOCUMENT,
            ResourceKind.ORGANIZATION_MEMBERSHIP,
        }:
            raise ValueError("Specialized ownership contract required.")


@dataclass(frozen=True)
class OrderResource(ResourceOwnership):
    # None explicitly represents ownership not established: never infer it.
    owner_user_id: str | None
    customer_id: str | None

    def __post_init__(self):
        super().__post_init__()
        if self.reference.kind is not ResourceKind.ORDER:
            raise ValueError("Order reference required.")
        for value in (self.owner_user_id, self.customer_id):
            if value is not None:
                require_identifier(value)


@dataclass(frozen=True)
class ConversationResource(ResourceOwnership):
    owner_user_id: str
    customer_id: str
    thread_id: str

    def __post_init__(self):
        super().__post_init__()
        if self.reference.kind not in {
            ResourceKind.CONVERSATION,
            ResourceKind.CHECKPOINT,
        }:
            raise ValueError("Conversation or checkpoint reference required.")
        for value in (self.owner_user_id, self.customer_id, self.thread_id):
            require_identifier(value)


@dataclass(frozen=True)
class OperationResource(ResourceOwnership):
    operation: TenantOperation
    target: ResourceReference
    owner_user_id: str
    customer_id: str | None

    def __post_init__(self):
        super().__post_init__()
        if self.reference.kind is not ResourceKind.IDEMPOTENT_OPERATION:
            raise ValueError("Operation reference required.")
        if not isinstance(self.operation, TenantOperation) or not isinstance(
            self.target, ResourceReference
        ):
            raise TypeError("Operation and exact target required.")
        if self.target.kind is ResourceKind.IDEMPOTENT_OPERATION:
            raise ValueError("Recursive operation target refused.")
        require_identifier(self.owner_user_id)
        if self.customer_id is not None:
            require_identifier(self.customer_id)


@dataclass(frozen=True)
class BuyerAssociation:
    """Explicit server-controlled human/org/buyer grant, not inferred ownership."""

    association_id: str
    organization_id: str
    user_id: str
    customer_id: str
    status: BuyerAssociationStatus
    revision: str

    def __post_init__(self):
        for value in (
            self.association_id,
            self.organization_id,
            self.user_id,
            self.customer_id,
            self.revision,
        ):
            require_identifier(value)
        if not isinstance(self.status, BuyerAssociationStatus):
            raise TypeError("Known buyer association status required.")


@dataclass(frozen=True)
class TenantOperationKey:
    """Future composite uniqueness contract; historical keys remain unchanged."""

    organization_id: str
    operation_id: str

    def __post_init__(self):
        require_identifier(self.organization_id)
        require_identifier(self.operation_id)
