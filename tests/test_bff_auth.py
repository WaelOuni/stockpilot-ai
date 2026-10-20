"""Pure BFF flow tests. No app main import, database, provider or real service."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Lock
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from stockpilot.application.ports.bff_security import (
    AuthenticatedIdentity,
    BffUnauthenticated,
    BffUnavailable,
)
from stockpilot.application.services.bff_sessions import BffSessionService
from stockpilot.bff import create_app
from stockpilot.domain.entities.identity import ExternalIdentity, User
from stockpilot.domain.entities.membership import Membership
from stockpilot.domain.entities.organization import Organization
from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.enums.role import Role
from stockpilot.infrastructure.config.bff_settings import BffSettings
from stockpilot.presentation.api.routes.bff_auth import (
    SESSION_COOKIE,
    TRANSACTION_COOKIE,
)

NOW = datetime(2026, 10, 20, tzinfo=UTC)


def test_expired_transaction_is_refused_before_exchange(env):
    state = begin(env)
    env[2][0] += timedelta(seconds=301)
    assert complete(env, state).status_code == 401
    assert env[1].provider.calls == 0


def test_concurrent_callback_has_only_one_winner(env):
    from concurrent.futures import ThreadPoolExecutor

    from stockpilot.application.ports.bff_security import BffUnauthenticated

    state = begin(env)
    browser = env[0].cookies.get(TRANSACTION_COOKIE)

    def attempt(_):
        try:
            return env[1].complete(state, browser, "one-code")
        except BffUnauthenticated:
            return None

    with ThreadPoolExecutor(max_workers=8) as workers:
        outcomes = list(workers.map(attempt, range(8)))
    assert sum(item is not None for item in outcomes) == 1
    assert env[1].provider.calls == 1


@pytest.mark.parametrize("name", [SESSION_COOKIE, TRANSACTION_COOKIE])
def test_duplicate_security_cookies_refused(env, name):
    state = begin(env)
    headers = {"Cookie": f"{name}={'a' * 43}; {name}={'b' * 43}"}
    path = "/b2b/auth/session" if name == SESSION_COOKIE else "/b2b/auth/callback"
    params = {} if name == SESSION_COOKIE else {"state": state, "code": "one-code"}
    assert env[0].get(path, params=params, headers=headers).status_code == 401


def test_callback_queries_scrubbed_even_for_unexpected_failure(env):
    captured = []

    def failed(*_args):
        raise RuntimeError("synthetic internal failure")

    env[1].complete = failed
    state = begin(env)
    app = env[0].app

    async def asgi(scope, receive, send):
        try:
            await app(scope, receive, send)
        finally:
            captured.append(scope.get("query_string"))

    client = TestClient(asgi, base_url=ORIGIN, raise_server_exceptions=False)
    client.cookies.update(env[0].cookies)
    result = client.get(
        "/b2b/auth/callback", params={"state": state, "code": "private-code"}
    )
    assert result.status_code == 500
    assert captured[-1] == b""
    assert "private-code" not in result.text


def test_disabled_user_revokes_existing_session(env):
    signed_in(env)
    env[3].user = replace(env[3].user, status=IdentityStatus.DISABLED)
    assert env[0].get("/b2b/auth/session").status_code == 403
    assert env[0].get("/b2b/auth/session").status_code == 401


ORIGIN = "https://bff.example.test"
LOGIN_HEADERS = {"Origin": ORIGIN, "X-BFF-CSRF": "login"}
IDENTITY = ExternalIdentity("https://id.example.test/realms/test", "fixture-subject")


class MemoryStore:
    """Test double only, never a selectable runtime backend."""

    def __init__(self):
        self.transactions = {}
        self.sessions = {}
        self.lock = Lock()

    def put_transaction(self, transaction):
        with self.lock:
            self.transactions[transaction.state_hash] = transaction

    def consume_transaction(self, state_hash, browser_hash, configuration, now):
        with self.lock:
            item = self.transactions.get(state_hash)
            if (
                not item
                or item.browser_hash != browser_hash
                or item.configuration != configuration
                or item.expires_at <= now
            ):
                return None
            return self.transactions.pop(state_hash)

    def rotate_session(self, session, previous_hash):
        with self.lock:
            self.sessions.pop(previous_hash, None)
            self.sessions[session.session_hash] = session

    def get_session(self, session_hash, configuration, now, idle_seconds):
        with self.lock:
            item = self.sessions.get(session_hash)
            if (
                not item
                or item.configuration != configuration
                or item.absolute_expires_at <= now
                or item.idle_expires_at <= now
            ):
                return None
            updated = replace(
                item,
                idle_expires_at=min(
                    item.absolute_expires_at,
                    now + timedelta(seconds=idle_seconds),
                ),
            )
            self.sessions[session_hash] = updated
            return updated

    def revoke_session(self, session_hash, configuration):
        with self.lock:
            self.sessions.pop(session_hash, None)


class Provider:
    def __init__(self):
        self.state = None
        self.calls = 0

    def authorization_url(self, state, nonce, verifier):
        self.state = state
        self.nonce = nonce
        self.verifier = verifier
        return "https://id.example.test/auth?" + urlencode(
            {"state": state, "nonce": nonce}
        )

    def exchange(self, code, nonce, verifier):
        self.calls += 1
        if code != "one-code" or nonce != self.nonce or verifier != self.verifier:
            raise BffUnauthenticated("Never expose private provider token")
        return AuthenticatedIdentity(IDENTITY, NOW + timedelta(seconds=300))

    def logout_url(self):
        return "https://id.example.test/logout?client_id=bff"


class IdentityRepositories:
    def __init__(self):
        self.user = User("u1", IDENTITY, IdentityStatus.ACTIVE, NOW, NOW)
        self.organization = Organization(
            "o1", "Fixture", OrganizationStatus.ACTIVE, NOW, NOW
        )
        self.membership = Membership(
            "m1", "u1", "o1", Role.USER, MembershipStatus.ACTIVE, NOW, NOW
        )

    def find_by_external_identity(self, identity):
        return (
            (self.user,)
            if self.user and self.user.external_identity == identity
            else ()
        )

    def get(self, organization_id):
        return (
            self.organization
            if self.organization
            and self.organization.organization_id == organization_id
            else None
        )

    def find_for_user(self, user_id):
        return (
            (self.membership,)
            if self.membership and self.membership.user_id == user_id
            else ()
        )


def settings(**changes):
    base = {
        "mode": "b2b_auth_test",
        "origin": ORIGIN,
        "issuer": IDENTITY.issuer,
        "client_id": "bff",
        "client_secret": "test-only-" + "s" * 32,
        "configuration_key": "test-only-" + "k" * 32,
        "mongo_uri": "mongodb://127.0.0.1:1",
        "mongo_database": "stockpilot_b2b_test_fixture",
        "session_idle_seconds": 60,
        "session_absolute_seconds": 300,
    }
    base.update(changes)
    return BffSettings(**base)


@pytest.fixture
def env():
    config = settings()
    provider, store, repos = Provider(), MemoryStore(), IdentityRepositories()
    now = [NOW]
    service = BffSessionService(
        provider,
        store,
        repos,
        repos,
        repos,
        config.validated_policy(),
        clock=lambda: now[0],
    )
    client = TestClient(
        create_app(config, service=service), base_url=ORIGIN, follow_redirects=False
    )
    return client, service, now, repos


def begin(env, organization="o1"):
    response = env[0].post(
        "/b2b/auth/login", json={"organization_id": organization}, headers=LOGIN_HEADERS
    )
    assert response.status_code == 303
    return env[1].provider.state


def complete(env, state=None):
    return env[0].get(
        "/b2b/auth/callback",
        params={
            "state": state or env[1].provider.state,
            "code": "one-code",
        },
    )


def signed_in(env):
    begin(env)
    response = complete(env)
    assert response.status_code == 303
    session = env[0].get("/b2b/auth/session")
    assert session.status_code == 200
    return session.json()


def test_full_flow_and_secure_cookies(env):
    state = begin(env)
    browser = env[0].cookies.get(TRANSACTION_COOKIE)
    assert len(state) == 43 and len(browser) == 43
    assert state not in repr(env[1].store.transactions)
    response = complete(env)
    assert response.status_code == 303
    assert response.headers["location"] == "/b2b/auth/session"
    cookie = response.headers.get_list("set-cookie")[0]
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag in cookie
    assert "Domain=" not in cookie
    result = env[0].get("/b2b/auth/me")
    assert result.status_code == 200 and result.json()["user_id"] == "u1"
    assert set(result.json()) == {
        "user_id",
        "organization_id",
        "role",
        "permissions",
        "csrf_token",
        "expires_at",
    }
    assert "access_token" not in result.text and "refresh_token" not in result.text
    stored = next(iter(env[1].store.sessions.values()))
    assert stored.absolute_expires_at == NOW + timedelta(seconds=300)
    assert env[0].cookies.get(SESSION_COOKIE) != stored.session_hash
    assert "no-store" in result.headers["cache-control"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://foreign"},
        {"Origin": ORIGIN},
        {"Origin": ORIGIN, "X-BFF-CSRF": "wrong"},
    ],
)
def test_login_csrf_and_origin(env, headers):
    response = env[0].post(
        "/b2b/auth/login", json={"organization_id": "o1"}, headers=headers
    )
    assert response.status_code == 403
    assert not env[1].store.transactions


def test_login_rejects_extra_privileges_and_large_body(env):
    response = env[0].post(
        "/b2b/auth/login",
        json={"organization_id": "o1", "role": "ORGANIZATION_ADMIN"},
        headers=LOGIN_HEADERS,
    )
    assert response.status_code == 401
    response = env[0].post(
        "/b2b/auth/login",
        content="x" * 5000,
        headers={**LOGIN_HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 401


def test_callback_state_binding_and_replay(env):
    state = begin(env)
    browser = env[0].cookies.get(TRANSACTION_COOKIE)
    other = TestClient(env[0].app, base_url=ORIGIN, follow_redirects=False)
    assert (
        other.get(
            "/b2b/auth/callback", params={"state": state, "code": "one-code"}
        ).status_code
        == 401
    )
    assert complete(env, "wrong-state").status_code == 401
    # A rejected callback clears the transaction cookie in this test browser.
    env[0].cookies.set(TRANSACTION_COOKIE, browser)
    assert complete(env, state).status_code == 303
    env[0].cookies.set(TRANSACTION_COOKIE, browser)
    assert complete(env, state).status_code == 401
    assert env[1].provider.calls == 1


@pytest.mark.parametrize(
    "query",
    [
        "state=a&state=b&code=one-code",
        "state=a&code=one-code&code=second",
        "state=a&code=one-code&error=access_denied",
        "state=a&code=one-code&redirect_uri=https://foreign",
    ],
)
def test_ambiguous_callback_refused(env, query):
    assert env[0].get("/b2b/auth/callback?" + query).status_code == 401
    assert env[1].provider.calls == 0


def test_provider_error_consumes_transaction_and_never_leaks_description(env):
    state = begin(env)
    browser = env[0].cookies.get(TRANSACTION_COOKIE)
    response = env[0].get(
        "/b2b/auth/callback",
        params={
            "state": state,
            "error": "access_denied",
            "error_description": "PRIVATE-CREDENTIAL",
        },
    )
    assert response.status_code == 401 and "PRIVATE" not in response.text
    env[0].cookies.set(TRANSACTION_COOKIE, browser)
    assert complete(env, state).status_code == 401


@pytest.mark.parametrize("query_issuer", ["https://foreign", ""])
def test_authorization_response_mixup_refused(env, query_issuer):
    state = begin(env)
    assert (
        env[0]
        .get(
            "/b2b/auth/callback",
            params={
                "state": state,
                "code": "one-code",
                "iss": query_issuer,
            },
        )
        .status_code
        == 401
    )


def test_missing_pkce_in_transaction_is_refused(env):
    begin(env)
    key, transaction = next(iter(env[1].store.transactions.items()))
    env[1].store.transactions[key] = replace(transaction, verifier="")
    assert complete(env).status_code == 401


@pytest.mark.parametrize(
    "cause",
    [
        "unknown_user",
        "disabled_user",
        "unknown_org",
        "suspended_org",
        "disabled_org",
        "missing_membership",
        "suspended_membership",
        "invited_membership",
    ],
)
def test_nonprovisioned_or_inactive_access_refused_without_creation(env, cause):
    _client, service, _now, repos = env
    begin(env)
    if cause == "unknown_user":
        repos.user = None
    elif cause == "disabled_user":
        repos.user = replace(repos.user, status=IdentityStatus.DISABLED)
    elif cause == "unknown_org":
        repos.organization = None
    elif cause == "suspended_org":
        repos.organization = replace(
            repos.organization, status=OrganizationStatus.SUSPENDED
        )
    elif cause == "disabled_org":
        repos.organization = replace(
            repos.organization, status=OrganizationStatus.DISABLED
        )
    elif cause == "missing_membership":
        repos.membership = None
    elif cause == "suspended_membership":
        repos.membership = replace(repos.membership, status=MembershipStatus.SUSPENDED)
    elif cause == "invited_membership":
        repos.membership = replace(repos.membership, status=MembershipStatus.INVITED)
    assert complete(env).status_code == 403
    assert not service.store.sessions


def test_foreign_organization_cannot_be_selected(env):
    begin(env, "foreign")
    assert complete(env).status_code == 403


@pytest.mark.parametrize("offset", [61, 301])
def test_idle_or_absolute_expiry(env, offset):
    signed_in(env)
    env[2][0] += timedelta(seconds=offset)
    assert env[0].get("/b2b/auth/session").status_code == 401


def test_idle_activity_never_extends_absolute_expiry(env):
    signed_in(env)
    for _ in range(5):
        env[2][0] += timedelta(seconds=50)
        assert env[0].get("/b2b/auth/session").status_code == 200
    env[2][0] += timedelta(seconds=50)
    assert env[0].get("/b2b/auth/session").status_code == 401


def test_logout_revokes_across_service_instances_and_cookie_replay(env):
    info = signed_in(env)
    cookie = env[0].cookies.get(SESSION_COOKIE)
    other_service = BffSessionService(
        env[1].provider,
        env[1].store,
        env[3],
        env[3],
        env[3],
        env[1].policy,
        clock=lambda: env[2][0],
    )
    assert other_service.current(cookie)[1].user_id == "u1"
    response = env[0].post(
        "/b2b/auth/logout", headers={"Origin": ORIGIN, "X-BFF-CSRF": info["csrf_token"]}
    )
    assert response.status_code == 200
    assert response.json()["provider_logout_url"].startswith(
        "https://id.example.test/logout"
    )
    with pytest.raises(BffUnauthenticated):
        other_service.current(cookie)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://foreign", "X-BFF-CSRF": "x"},
        {"Origin": ORIGIN},
        {"Origin": ORIGIN, "X-BFF-CSRF": "wrong"},
    ],
)
def test_logout_csrf_is_session_bound(env, headers):
    signed_in(env)
    assert env[0].post("/b2b/auth/logout", headers=headers).status_code == 403
    assert len(env[1].store.sessions) == 1


def test_rotation_and_old_cookie_replay(env):
    signed_in(env)
    old = env[0].cookies.get(SESSION_COOKIE)
    signed_in(env)
    new = env[0].cookies.get(SESSION_COOKIE)
    assert new != old and len(env[1].store.sessions) == 1
    with pytest.raises(BffUnauthenticated):
        env[1].current(old)


def test_suspended_membership_revokes_existing_session(env):
    signed_in(env)
    env[3].membership = replace(env[3].membership, status=MembershipStatus.SUSPENDED)
    assert env[0].get("/b2b/auth/session").status_code == 403
    assert env[0].get("/b2b/auth/session").status_code == 401


def test_storage_outage_is_503_not_403_and_details_are_private(env):
    signed_in(env)

    def unavailable(*args):
        raise BffUnavailable("PRIVATE-URI-SECRET")

    env[1].store.get_session = unavailable
    response = env[0].get("/b2b/auth/session")
    assert response.status_code == 503 and "PRIVATE" not in response.text


def test_provider_outage_after_logout_does_not_restore_session(env):
    info = signed_in(env)
    old = env[0].cookies.get(SESSION_COOKIE)

    def unavailable():
        raise BffUnavailable("private-provider-secret")

    env[1].provider.logout_url = unavailable
    assert (
        env[0]
        .post(
            "/b2b/auth/logout",
            headers={"Origin": ORIGIN, "X-BFF-CSRF": info["csrf_token"]},
        )
        .status_code
        == 503
    )
    with pytest.raises(BffUnauthenticated):
        env[1].current(old)


@pytest.mark.parametrize(
    "path",
    [
        "/products",
        "/assistant",
        "/chat",
        "/classify",
        "/orders",
        "/customer/orders/x/cancel",
        "/backoffice/products",
        "/backoffice/session/login",
        "/docs",
        "/openapi.json",
    ],
)
def test_bff_identity_cannot_access_global_business_routes(env, path):
    signed_in(env)
    assert env[0].get(path).status_code == 404
    assert env[0].post(path, json={}).status_code == 404


def test_default_mode_is_disabled_without_dependencies():
    app = create_app(BffSettings(_env_file=None))
    client = TestClient(app, base_url=ORIGIN)
    assert client.get("/health").status_code == 200
    assert client.get("/b2b/auth/session").status_code == 503


def test_auth_only_entrypoint_does_not_initialize_legacy_application():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from stockpilot.bff import create_app; "
                "from stockpilot.infrastructure.config.bff_settings import BffSettings; "
                "create_app(BffSettings(mode='disabled')); "
                "assert 'stockpilot.main' not in sys.modules; "
                "assert not any('workflow' in name or 'pgvector' in name for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_incomplete_mode_is_fail_closed():
    client = TestClient(create_app(BffSettings(mode="b2b_auth_test")), base_url=ORIGIN)
    assert client.get("/b2b/auth/session").status_code == 503


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "http://localhost"},
        {"origin": "https://bff.example.test/"},
        {"issuer": "http://remote.example/realms/test"},
        {"issuer": "https://id.example.test/realms/test/"},
        {"client_secret": "short"},
        {"configuration_key": None},
        {"mongo_database": "stockpilot"},
        {"session_idle_seconds": 400},
    ],
)
def test_unsafe_configuration_refused(changes):
    with pytest.raises(BffUnavailable):
        settings(**changes).validated_policy()


def test_http_or_wrong_host_is_refused(env):
    signed_in(env)
    assert env[0].get("http://bff.example.test/b2b/auth/session").status_code == 400
    assert (
        env[0].get("/b2b/auth/session", headers={"Host": "foreign"}).status_code == 400
    )


def test_historical_bearer_is_not_a_bff_session(env):
    assert (
        env[0]
        .get("/b2b/auth/session", headers={"Authorization": "Bearer historical"})
        .status_code
        == 401
    )
