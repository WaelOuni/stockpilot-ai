"""OIDC/BFF orchestration. Token material never leaves the provider adapter."""

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from stockpilot.application.ports.bff_security import (
    BffSession,
    BffStore,
    BffUnauthenticated,
    BffUnavailable,
    LoginTransaction,
    OidcProvider,
)
from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.services.identity_authorization import (
    IdentityAuthorizationService,
    IdentityResolutionError,
)
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.domain.entities.principal import Principal


class BffForbidden(Exception):
    """Authenticated human does not have active provisioned application access."""


def token_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def opaque_token(value: str | None) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None
    )


@dataclass(frozen=True)
class BffPolicy:
    configuration: str
    transaction_seconds: int = 300
    absolute_seconds: int = 1800
    idle_seconds: int = 600


@dataclass(frozen=True)
class _HumanEvidence:
    identity: ExternalIdentity


class _AuthenticatedHuman:
    def __init__(self, evidence: _HumanEvidence):
        self.evidence = evidence

    def authenticate(self, request_context: object) -> ExternalIdentity | None:
        return self.evidence.identity if request_context is self.evidence else None


class BffSessionService:
    def __init__(
        self,
        provider: OidcProvider,
        store: BffStore,
        identities,
        organizations,
        memberships,
        policy: BffPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.provider = provider
        self.store = store
        self.identities = identities
        self.organizations = organizations
        self.memberships = memberships
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(UTC))

    def _resolve(self, identity: ExternalIdentity, organization_id: str) -> Principal:
        evidence = _HumanEvidence(identity)
        resolver = IdentityAuthorizationService(
            _AuthenticatedHuman(evidence),
            self.identities,
            self.organizations,
            self.memberships,
        )
        try:
            return resolver.resolve(evidence, organization_id)
        except IdentityRepositoryError:
            raise BffUnavailable("Identity storage unavailable.") from None
        except IdentityResolutionError:
            raise BffForbidden("Active provisioned membership required.") from None

    def begin(
        self, organization_id: str, previous_cookie: str | None
    ) -> tuple[str, str]:
        # Organization is only a requested choice; checked against stores after OIDC.
        if (
            not isinstance(organization_id, str)
            or not organization_id
            or organization_id != organization_id.strip()
            or len(organization_id) > 128
        ):
            raise BffForbidden("Explicit organization required.")
        state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        url = self.provider.authorization_url(state, nonce, verifier)
        self.store.put_transaction(
            LoginTransaction(
                token_hash(state),
                token_hash(browser),
                nonce,
                verifier,
                organization_id,
                self.clock() + timedelta(seconds=self.policy.transaction_seconds),
                self.policy.configuration,
                token_hash(previous_cookie) if opaque_token(previous_cookie) else None,
            )
        )
        return url, browser

    def complete(
        self,
        state: str,
        browser: str | None,
        code: str | None,
        provider_error: bool = False,
    ) -> str:
        if not opaque_token(state) or not opaque_token(browser):
            raise BffUnauthenticated("Invalid authentication transaction.")
        transaction = self.store.consume_transaction(
            token_hash(state),
            token_hash(browser),
            self.policy.configuration,
            self.clock(),
        )
        # Consumption precedes token exchange so concurrent callbacks cannot replay.
        if transaction is None or provider_error or not code or len(code) > 4096:
            raise BffUnauthenticated("Invalid authentication transaction.")
        if (
            not transaction.verifier
            or not transaction.nonce
            or transaction.configuration != self.policy.configuration
        ):
            raise BffUnauthenticated("Incomplete authentication transaction.")
        verified = self.provider.exchange(code, transaction.nonce, transaction.verifier)
        principal = self._resolve(verified.identity, transaction.organization_id)
        now = self.clock()
        expiry = min(
            verified.expires_at, now + timedelta(seconds=self.policy.absolute_seconds)
        )
        if expiry <= now:
            raise BffUnauthenticated("Authentication expired.")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        session = BffSession(
            token_hash(token),
            verified.identity,
            principal.user_id,
            principal.organization_id,
            csrf,
            now,
            expiry,
            min(expiry, now + timedelta(seconds=self.policy.idle_seconds)),
            self.policy.configuration,
        )
        self.store.rotate_session(session, transaction.previous_session_hash)
        return token

    def current(self, cookie: str | None) -> tuple[BffSession, Principal]:
        if not opaque_token(cookie):
            raise BffUnauthenticated("Session absent or expired.")
        session = self.store.get_session(
            token_hash(cookie),
            self.policy.configuration,
            self.clock(),
            self.policy.idle_seconds,
        )
        if session is None:
            raise BffUnauthenticated("Session absent or expired.")
        try:
            principal = self._resolve(session.identity, session.organization_id)
            if principal.user_id != session.user_id:
                raise BffForbidden("Session identity changed.")
        except BffForbidden:
            self.store.revoke_session(session.session_hash, self.policy.configuration)
            raise
        return session, principal

    def organization_name(self, organization_id: str) -> str:
        """Display metadata from the same trusted store, never from browser claims."""
        try:
            organization = self.organizations.get(organization_id)
        except IdentityRepositoryError:
            raise BffUnavailable("Identity storage unavailable.") from None
        if organization is None:
            raise BffForbidden("Active provisioned organization required.")
        return organization.name

    def logout(self, cookie: str | None, csrf: str) -> str:
        session, _ = self.current(cookie)
        if not secrets.compare_digest(csrf.encode(), session.csrf_token.encode()):
            raise BffForbidden("Invalid CSRF protection.")
        self.store.revoke_session(session.session_hash, self.policy.configuration)
        # Local revocation has already succeeded even if provider discovery fails.
        return self.provider.logout_url()
