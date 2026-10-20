from typing import Protocol
from stockpilot.domain.entities.identity import ExternalIdentity, User

class IdentityRepository(Protocol):
    def find_by_external_identity(self, identity: ExternalIdentity) -> User | None: ...
