from typing import Protocol

from stockpilot.domain.entities.identity import ExternalIdentity, User


class IdentityRepository(Protocol):
    def find_by_external_identity(self, identity: ExternalIdentity) -> tuple[User, ...]:
        """Return all exact matches; resolver denies duplicates.

        Future persistence must enforce unique user_id and unique (issuer, subject).
        Raise IdentityRepositoryError on technical failure, never return an empty
        result to hide an unavailable store.
        """
        ...
