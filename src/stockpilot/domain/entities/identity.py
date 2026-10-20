"""Domain identity keys are data, never evidence of authentication."""

from dataclasses import dataclass
from datetime import datetime

from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.identity_validation import require_dates, require_identifier


@dataclass(frozen=True)
class ExternalIdentity:
    """Exact (issuer, subject) key; no email, roles, or 'verified' caller flag."""

    issuer: str
    subject: str

    def __post_init__(self) -> None:
        require_identifier(self.issuer)
        require_identifier(self.subject)


@dataclass(frozen=True)
class User:
    user_id: str
    external_identity: ExternalIdentity
    status: IdentityStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.user_id)
        if not isinstance(self.external_identity, ExternalIdentity):
            raise TypeError("An external identity key is required.")
        if not isinstance(self.status, IdentityStatus):
            raise TypeError("Unknown user status.")
        require_dates(self.created_at, self.updated_at)
