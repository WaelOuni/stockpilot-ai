from dataclasses import dataclass
from datetime import datetime

from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.identity_validation import require_dates, require_identifier


@dataclass(frozen=True)
class Organization:
    organization_id: str
    name: str
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.organization_id)
        require_identifier(self.name)
        if not isinstance(self.status, OrganizationStatus):
            raise TypeError("Unknown organization status.")
        require_dates(self.created_at, self.updated_at)
