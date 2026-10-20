from dataclasses import dataclass

from stockpilot.domain.enums.permission import Permission
from stockpilot.domain.enums.role import Role
from stockpilot.domain.identity_validation import require_identifier


@dataclass(frozen=True)
class Principal:
    """Human request context. Trusted service must recheck it before authorization."""

    user_id: str
    issuer: str
    subject: str
    organization_id: str
    membership_id: str
    role: Role
    permissions: frozenset[Permission]

    def __post_init__(self) -> None:
        for value in (
            self.user_id,
            self.issuer,
            self.subject,
            self.organization_id,
            self.membership_id,
        ):
            require_identifier(value)
        if not isinstance(self.role, Role):
            raise TypeError("Unknown principal role.")
        if not isinstance(self.permissions, frozenset) or any(
            not isinstance(p, Permission) for p in self.permissions
        ):
            raise ValueError(
                "Permissions must be an immutable set of known permissions."
            )


@dataclass(frozen=True)
class ResourceScope:
    """Scope read from trusted resource storage, never an authorization header."""

    organization_id: str
    owner_user_id: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.organization_id)
        if self.owner_user_id is not None:
            require_identifier(self.owner_user_id)
