from enum import StrEnum


class Permission(StrEnum):
    RAG_READ = "rag:read"
    RAG_MANAGE = "rag:manage"
    CONVERSATION_OWN = "conversation:own"
    CATALOGUE_READ = "catalogue:read"
    STOCK_READ = "stock:read"
    STOCK_ADJUST = "stock:adjust"
    ORDER_CREATE = "order:create"
    ORDER_READ_OWN = "order:read_own"
    ORDER_READ_ORGANIZATION = "order:read_organization"
    ORDER_CANCEL_OWN = "order:cancel_own"
    ORDER_CANCEL_ORGANIZATION = "order:cancel_organization"
    IMPORT_EXECUTE = "import:execute"
    ORGANIZATION_MANAGE_MEMBERS = "organization:manage_members"
    CONTACT_CREATE = "contact:create"
    CONTACT_READ_OWN = "contact:read_own"
    CONTACT_READ_ORGANIZATION = "contact:read_organization"
    CONTACT_REPLY_ORGANIZATION = "contact:reply_organization"
    CONTACT_MANAGE_ORGANIZATION = "contact:manage_organization"
