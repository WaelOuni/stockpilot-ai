"""BFF contracts; no HTTP, MongoDB or token-validation dependencies."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from stockpilot.domain.entities.identity import ExternalIdentity


class BffUnauthenticated(Exception):
    """Invalid credentials, callback or expired/unknown application session."""


class BffUnavailable(Exception):
    """Required identity infrastructure is unavailable or misconfigured."""


@dataclass(frozen=True)
class LoginTransaction:
    state_hash: str
    browser_hash: str
    nonce: str = field(repr=False)
    verifier: str = field(repr=False)
    organization_id: str
    expires_at: datetime
    configuration: str
    previous_session_hash: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class BffSession:
    session_hash: str
    identity: ExternalIdentity
    user_id: str
    organization_id: str
    csrf_token: str = field(repr=False)
    created_at: datetime
    absolute_expires_at: datetime
    idle_expires_at: datetime
    configuration: str
    provider_expires_at: datetime | None = None
    refresh_ciphertext: str | None = field(default=None, repr=False)
    refresh_version: int = 0


@dataclass(frozen=True)
class AuthenticatedIdentity:
    identity: ExternalIdentity
    expires_at: datetime
    refresh_token: str | None = field(default=None, repr=False)
    auth_time: float | None = None
    audience: tuple[str, ...] = ()


class TokenProtector(Protocol):
    def seal(self, token: str, nonce: str, binding: str) -> str: ...
    def open(self, ciphertext: str, binding: str) -> tuple[str, str]: ...


class OidcProvider(Protocol):
    def authorization_url(self, state: str, nonce: str, verifier: str) -> str: ...
    def exchange(
        self, code: str, nonce: str, verifier: str
    ) -> AuthenticatedIdentity: ...
    def refresh(self, token: str, nonce: str) -> AuthenticatedIdentity: ...
    def logout_url(self) -> str: ...


class BffStore(Protocol):
    def put_transaction(self, transaction: LoginTransaction) -> None: ...
    def consume_transaction(
        self, state_hash: str, browser_hash: str, configuration: str, now: datetime
    ) -> LoginTransaction | None:
        """Atomically remove and return a matching live transaction once."""
        ...

    def rotate_session(self, session: BffSession, previous_hash: str | None) -> None:
        """Atomically create new session and revoke the browser's previous one."""
        ...

    def get_session(
        self, session_hash: str, configuration: str, now: datetime, idle_seconds: int
    ) -> BffSession | None:
        """Reject expiry and atomically extend idle expiry, capped by absolute."""
        ...

    def revoke_session(self, session_hash: str, configuration: str) -> None: ...

    def claim_refresh(self, session: BffSession, owner: str, now: datetime) -> bool: ...
    def finish_refresh(self, session: BffSession, owner: str, ciphertext: str, expires: datetime, now: datetime) -> bool: ...
