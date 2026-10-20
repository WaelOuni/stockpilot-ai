from enum import StrEnum


class Role(StrEnum):
    READ_ONLY = "READ_ONLY"
    USER = "USER"
    MANAGER = "MANAGER"
    ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN"
