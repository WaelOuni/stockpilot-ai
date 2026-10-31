"""OIDC/BFF orchestration. Renewal credentials remain protected on the server."""

import re
import json
import time
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
    TokenProtector,
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
    absolute_seconds: int = 3600
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
        protector: TokenProtector | None = None,
    ):
        self.protector = protector
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
        renewing = self.protector is not None
        if renewing:
            if not verified.refresh_token:
                raise BffUnauthenticated("Renewal credentials required.")
            expiry = now + timedelta(seconds=self.policy.absolute_seconds)
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
        if renewing:
            from dataclasses import replace
            session = replace(session, provider_expires_at=verified.expires_at,
                refresh_ciphertext=self.protector.seal(verified.refresh_token,
                    json.dumps({"nonce": transaction.nonce, "auth_time": verified.auth_time, "audience": verified.audience}), self._binding(session)))
        self.store.rotate_session(session, transaction.previous_session_hash)
        return token

    @staticmethod
    def _binding(session):
        return json.dumps([session.session_hash, session.configuration, session.user_id,
            session.organization_id, session.identity.issuer, session.identity.subject], separators=(",", ":"))

    def _renew(self, session):
        if session.provider_expires_at is None:  # Legacy sessions retain their original cap.
            return session
        if self.protector is None or not session.refresh_ciphertext:
            raise BffUnauthenticated("Renewal unavailable.")
        if session.provider_expires_at > self.clock() + timedelta(seconds=30):
            return session
        # One durable claimant only. Never take over an uncertain token grant:
        # a worker could have consumed a rotating token before disappearing.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            owner = secrets.token_urlsafe(32)
            if self.store.claim_refresh(session, owner, self.clock()):
                try:
                    token, encoded = self.protector.open(session.refresh_ciphertext, self._binding(session))
                    metadata = json.loads(encoded)
                    fresh = self.provider.refresh(token, metadata["nonce"])
                    if fresh.identity != session.identity or fresh.audience != tuple(metadata["audience"]) or (fresh.auth_time is not None and fresh.auth_time != metadata["auth_time"]):
                        raise BffUnauthenticated("Renewed identity mismatch.")
                    if fresh.expires_at <= self.clock():
                        raise BffUnauthenticated("Renewed identity expired.")
                    ciphertext = self.protector.seal(fresh.refresh_token or token, encoded, self._binding(session))
                    if not self.store.finish_refresh(session, owner, ciphertext, fresh.expires_at, self.clock()):
                        raise BffUnauthenticated("Session revoked or expired during renewal.")
                except Exception as error:
                    # Fail closed even for ambiguous transport/storage outcomes;
                    # no automatic retry of a potentially consumed refresh token.
                    self.store.revoke_session(session.session_hash, self.policy.configuration)
                    if isinstance(error, (BffUnauthenticated, BffUnavailable)):
                        raise
                    raise BffUnauthenticated("Renewal failed.") from None
            else:
                time.sleep(0.025)
            current = self.store.get_session(session.session_hash, self.policy.configuration,
                self.clock(), self.policy.idle_seconds)
            if current is None:
                raise BffUnauthenticated("Session revoked or expired.")
            if current.provider_expires_at > self.clock() and current.refresh_version != session.refresh_version:
                return current
            session = current
        raise BffUnavailable("Session renewal in progress. Retry a read explicitly.")

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
        session = self._renew(session)
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
        # Logout must not wait on or start a provider refresh (including in flight).
        if not opaque_token(cookie):
            raise BffUnauthenticated("Session absent or expired.")
        session = self.store.get_session(token_hash(cookie), self.policy.configuration,
            self.clock(), self.policy.idle_seconds)
        if session is None:
            raise BffUnauthenticated("Session absent or expired.")
        if not secrets.compare_digest(csrf.encode(), session.csrf_token.encode()):
            raise BffForbidden("Invalid CSRF protection.")
        self.store.revoke_session(session.session_hash, self.policy.configuration)
        # Local revocation has already succeeded even if provider discovery fails.
        return self.provider.logout_url()
