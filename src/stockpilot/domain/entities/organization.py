from dataclasses import dataclass
from datetime import datetime
from stockpilot.domain.enums.organization_status import OrganizationStatus

@dataclass(frozen=True)
class Organization:
    organization_id: str
    name: str
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime
    def __post_init__(self):
        if not self.organization_id.strip() or not self.name.strip(): raise ValueError("organization_id and name are required")
