from dataclasses import dataclass
from datetime import datetime
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.role import Role

@dataclass(frozen=True)
class Membership:
    membership_id: str
    user_id: str
    organization_id: str
    role: Role
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime
    def __post_init__(self):
        if not self.membership_id.strip() or not self.user_id.strip() or not self.organization_id.strip(): raise ValueError("membership identifiers are required")
