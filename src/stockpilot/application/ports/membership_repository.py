from typing import Protocol

from stockpilot.domain.entities.membership import Membership


class MembershipRepository(Protocol):
    def find_for_user(self, user_id: str) -> tuple[Membership, ...]:
        """Return all statuses; resolver checks consistency and duplicate actives.

        Future persistence must enforce unique membership_id and at most one
        active (user_id, organization_id). Technical errors must propagate.
        """
        ...
