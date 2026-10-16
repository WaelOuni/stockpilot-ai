"""Storage contract for existing local operator sessions."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Session:
    username: str
    csrf_token: str
    expires_at: float


class SessionStoreUnavailable(Exception):
    pass


class SessionCapacityReached(Exception):
    pass


class LoginAttemptsExceeded(Exception):
    pass


class AdminSessionStore(Protocol):
    def configure(self, fingerprint: str) -> None: ...

    def attempt(self, address: str) -> None: ...

    def get(self, token: str, fingerprint: str) -> Session | None: ...

    def create(self, username: str, previous: str, ttl: int, capacity: int,
               fingerprint: str) -> tuple[str, Session]: ...

    def revoke(self, token: str, fingerprint: str) -> None: ...
