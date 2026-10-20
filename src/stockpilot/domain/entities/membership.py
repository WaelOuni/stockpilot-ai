from dataclasses import dataclass
from datetime import datetime

from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.role import Role
from stockpilot.domain.identity_validation import require_dates, require_identifier


@dataclass(frozen=True)
class Membership:
    membership_id: str
    user_id: str
    organization_id: str
    role: Role
    status: MembershipStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for value in (self.membership_id, self.user_id, self.organization_id):
            require_identifier(value)
        if not isinstance(self.role, Role) or not isinstance(
            self.status, MembershipStatus
        ):
            raise TypeError("Unknown membership role or status.")
        require_dates(self.created_at, self.updated_at)
