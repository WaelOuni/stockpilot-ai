"""Explicit operations and outcomes; reserved operations grant no permissions."""

from enum import StrEnum


class ResourceKind(StrEnum):
    CATALOGUE_ITEM = "catalogue_item"
    STOCK_ITEM = "stock_item"
    ORDER = "order"
    IMPORT_JOB = "import_job"
    RAG_DOCUMENT = "rag_document"
    CONVERSATION = "conversation"
    CHECKPOINT = "checkpoint"
    IDEMPOTENT_OPERATION = "idempotent_operation"
    ORGANIZATION_MEMBERSHIP = "organization_membership"


class TenantOperation(StrEnum):
    CATALOGUE_READ = "catalogue_read"
    STOCK_READ = "stock_read"
    STOCK_ADJUST = "stock_adjust"
    ORDER_READ_OWN = "order_read_own"
    ORDER_READ_ORGANIZATION = "order_read_organization"
    ORDER_CANCEL_OWN = "order_cancel_own"
    ORDER_CANCEL_ORGANIZATION = "order_cancel_organization"
    IMPORT_EXECUTE = "import_execute"
    MANAGE_MEMBERS = "manage_members"
    # Reserved: no approved permission/atomic repository contract in this phase.
    ORDER_CREATE = "order_create"
    RAG_READ = "rag_read"
    CONVERSATION_READ = "conversation_read"
    CHECKPOINT_READ = "checkpoint_read"
    IDEMPOTENCY_REPLAY = "idempotency_replay"


class AuthorizationDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_FOUND = "not_found"
    INDETERMINATE = "indeterminate"

    def __bool__(self) -> bool:
        raise TypeError(
            "Compare the decision explicitly with AuthorizationDecision.ALLOW."
        )


class BuyerAssociationStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
