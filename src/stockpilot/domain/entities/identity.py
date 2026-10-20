from dataclasses import dataclass
from datetime import datetime
from stockpilot.domain.enums.identity_status import IdentityStatus

@dataclass(frozen=True)
class ExternalIdentity:
    issuer: str
    subject: str
    def __post_init__(self):
        if not self.issuer.strip() or not self.subject.strip(): raise ValueError("issuer and subject are required")

@dataclass(frozen=True)
class User:
    user_id: str
    external_identity: ExternalIdentity
    status: IdentityStatus
    created_at: datetime
    updated_at: datetime
    def __post_init__(self):
        if not self.user_id.strip(): raise ValueError("user_id is required")
