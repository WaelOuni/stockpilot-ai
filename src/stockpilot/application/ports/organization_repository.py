from typing import Protocol

from stockpilot.domain.entities.organization import Organization


class OrganizationRepository(Protocol):
    def get(self, organization_id: str) -> Organization | None:
        """Exact unique ID lookup; ambiguous records raise IdentityRepositoryError."""
        ...
