"""OIDC adapter tests with real RSA signatures and an isolated HTTP transport."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from stockpilot.application.ports.bff_security import BffUnauthenticated, BffUnavailable
from stockpilot.infrastructure.identity.oidc_provider import OidcHttpProvider

ISSUER = "https://identity.example/realms/stockpilot"
CLIENT_ID = "stockpilot-bff"
SECRET = "client-secret-never-visible"
NONCE = "nonce-expected"
VERIFIER = "v" * 64


@pytest.fixture(scope="module")
def signing_keys():
    return tuple(
        rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2)
    )


def jwk(key, kid="first"):
    return {
        **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())),
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
    }


def claims(**changes):
    now = int(time.time())
    return {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "user-123",
        "iat": now - 5,
        "exp": now + 300,
        "nonce": NONCE,
        **changes,
    }


@pytest.fixture
def harness(signing_keys):
    first, second = signing_keys
    state = {
        "discovery": {
            "issuer": ISSUER,
            "authorization_endpoint": ISSUER + "/authorize",
            "token_endpoint": ISSUER + "/token",
            "jwks_uri": ISSUER + "/keys",
            "end_session_endpoint": ISSUER + "/logout",
        },
        "jwks": {"keys": [jwk(first)]},
        "token": jwt.encode(
            claims(), first, algorithm="RS256", headers={"kid": "first"}
        ),
        "requests": [],
        "status": {},
        "raw": {},
        "failure": None,
    }

    def handler(request):
        state["requests"].append(request)
        route = request.url.path.rsplit("/", 1)[-1]
        if state["failure"] == route:
            raise httpx.ReadTimeout("contains-provider-secrets", request=request)
        status = state["status"].get(route, 200)
        if route in state["raw"]:
            return httpx.Response(status, content=state["raw"][route])
        if route == "token" and status == 400:
            return httpx.Response(status, json={"error": "invalid_grant"})
        bodies = {
            "openid-configuration": state["discovery"],
            "keys": state["jwks"],
            "token": {
                "id_token": state["token"],
                "access_token": "access-token-secret",
                "refresh_token": "refresh-token-secret",
            },
        }
        assert route in bodies
        return httpx.Response(status, json=bodies[route])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OidcHttpProvider(
        ISSUER,
        CLIENT_ID,
        SECRET,
        "https://app.example/auth/callback",
        "https://app.example/",
        client=client,
    )
    yield provider, state, first, second
    client.close()


def exchange(provider):
    return provider.exchange("single-use-code", NONCE, VERIFIER)


def test_code_flow_pkce_and_private_exchange(harness):
    provider, state, _, _ = harness
    url = provider.authorization_url("state-value", NONCE, VERIFIER)
    query = parse_qs(urlsplit(url).query)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert query == {
        "response_type": ["code"],
        "client_id": [CLIENT_ID],
        "redirect_uri": ["https://app.example/auth/callback"],
        "scope": ["openid"],
        "state": ["state-value"],
        "nonce": [NONCE],
        "code_challenge": [expected],
        "code_challenge_method": ["S256"],
    }
    identity = exchange(provider)
    assert (
        identity.identity.issuer == ISSUER and identity.identity.subject == "user-123"
    )
    request = next(r for r in state["requests"] if r.method == "POST")
    assert parse_qs(request.content.decode()) == {
        "grant_type": ["authorization_code"],
        "code": ["single-use-code"],
        "redirect_uri": ["https://app.example/auth/callback"],
        "code_verifier": [VERIFIER],
    }
    assert request.headers["authorization"].startswith("Basic ")
    assert request.extensions["timeout"]["read"] == 10.0
    assert all(
        s not in repr(identity)
        for s in ("access-token-secret", "refresh-token-secret", state["token"])
    )
    assert SECRET not in url and SECRET not in repr(provider)


def test_metadata_and_keys_cached(harness):
    provider, state, _, _ = harness
    exchange(provider)
    exchange(provider)
    paths = [r.url.path.rsplit("/", 1)[-1] for r in state["requests"]]
    assert (
        paths.count("openid-configuration") == 1
        and paths.count("keys") == 1
        and paths.count("token") == 2
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://attacker.example"},
        {"aud": "different-client"},
        {"exp": 1},
        {"iat": 9999999999},
        {"nbf": 9999999999},
        {"nonce": "wrong"},
        {"nonce": 42},
        {"nonce": ""},
        {"sub": " "},
        {"exp": "9999999999"},
        {"exp": True},
        {"iat": True},
        {"iat": "1"},
        {"nbf": "1"},
        {"azp": "other-client"},
        {"aud": [CLIENT_ID, "another-client"]},
        {"aud": [CLIENT_ID, "another-client"], "azp": "wrong"},
    ],
)
def test_invalid_claims(harness, changes):
    provider, state, first, _ = harness
    state["token"] = jwt.encode(
        claims(**changes), first, algorithm="RS256", headers={"kid": "first"}
    )
    with pytest.raises(BffUnauthenticated):
        exchange(provider)


@pytest.mark.parametrize("missing", ["iss", "aud", "sub", "exp", "iat", "nonce"])
def test_required_claims(harness, missing):
    provider, state, first, _ = harness
    payload = claims()
    del payload[missing]
    state["token"] = jwt.encode(
        payload, first, algorithm="RS256", headers={"kid": "first"}
    )
    with pytest.raises(BffUnauthenticated):
        exchange(provider)


def test_multiple_audiences_require_authorized_party(harness):
    provider, state, first, _ = harness
    state["token"] = jwt.encode(
        claims(aud=[CLIENT_ID, "api"], azp=CLIENT_ID),
        first,
        algorithm="RS256",
        headers={"kid": "first"},
    )
    assert exchange(provider).identity.subject == "user-123"


def test_invalid_signature(harness):
    provider, state, _, second = harness
    state["token"] = jwt.encode(
        claims(), second, algorithm="RS256", headers={"kid": "first"}
    )
    with pytest.raises(BffUnauthenticated):
        exchange(provider)


def test_algorithm_confusion_refused_before_jwks(harness):
    provider, state, _, _ = harness
    state["token"] = jwt.encode(
        claims(),
        "symmetric-secret-for-test-with-32-bytes",
        algorithm="HS256",
        headers={"kid": "first"},
    )
    with pytest.raises(BffUnauthenticated):
        exchange(provider)
    assert not any(r.url.path.endswith("/keys") for r in state["requests"])


def test_key_rotation_and_unknown_kid_cooldown(harness, monkeypatch):
    provider, state, _, second = harness
    clock = [1000.0]
    monkeypatch.setattr(
        "stockpilot.infrastructure.identity.oidc_provider.time.monotonic",
        lambda: clock[0],
    )
    exchange(provider)
    state["jwks"] = {"keys": [jwk(second, "second")]}
    state["token"] = jwt.encode(
        claims(), second, algorithm="RS256", headers={"kid": "second"}
    )
    with pytest.raises(BffUnauthenticated):
        exchange(provider)
    clock[0] += 11
    assert exchange(provider).identity.subject == "user-123"
    state["token"] = jwt.encode(
        claims(), second, algorithm="RS256", headers={"kid": "never-known"}
    )
    for _ in range(5):
        with pytest.raises(BffUnauthenticated):
            exchange(provider)
    assert sum(r.url.path.endswith("/keys") for r in state["requests"]) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "https://attacker.example"),
        ("token_endpoint", "https://attacker.example/token"),
        ("authorization_endpoint", "https://attacker.example/authorize"),
        ("jwks_uri", "https://attacker.example/keys"),
        ("end_session_endpoint", "https://attacker.example/logout"),
        ("token_endpoint", "https://user:secret@identity.example/token"),
        ("token_endpoint", "http://identity.example/token"),
        ("token_endpoint", ISSUER + "/token#fragment"),
    ],
)
def test_untrusted_discovery_never_sends_credentials(harness, field, value):
    provider, state, _, _ = harness
    state["discovery"][field] = value
    with pytest.raises(BffUnavailable):
        exchange(provider)
    assert (
        len(state["requests"]) == 1
        and "authorization" not in state["requests"][0].headers
    )


@pytest.mark.parametrize(
    "route,status,error",
    [
        ("openid-configuration", 500, BffUnavailable),
        ("token", 500, BffUnavailable),
        ("token", 400, BffUnauthenticated),
        ("token", 401, BffUnavailable),
        ("token", 302, BffUnavailable),
        ("keys", 503, BffUnavailable),
    ],
)
def test_http_failures_sanitized(harness, route, status, error):
    provider, state, _, _ = harness
    state["status"][route] = status
    with pytest.raises(error) as caught:
        exchange(provider)
    assert all(
        s not in str(caught.value)
        for s in (
            SECRET,
            "single-use-code",
            "access-token-secret",
            "refresh-token-secret",
        )
    )


@pytest.mark.parametrize("route", ["openid-configuration", "token", "keys"])
def test_timeout_sanitized(harness, route):
    provider, state, _, _ = harness
    state["failure"] = route
    with pytest.raises(BffUnavailable) as caught:
        exchange(provider)
    assert "contains-provider-secrets" not in str(caught.value)


@pytest.mark.parametrize(
    "route,body",
    [
        ("openid-configuration", b"[1]"),
        ("token", b"not-json"),
        ("keys", b"{}"),
        ("token", b"x" * 131073),
    ],
)
def test_invalid_or_oversized_response(harness, route, body):
    provider, state, _, _ = harness
    state["raw"][route] = body
    with pytest.raises(BffUnavailable):
        exchange(provider)


@pytest.mark.parametrize(
    "variant", ["duplicate", "many", "wrong_type", "wrong_alg", "wrong_use"]
)
def test_invalid_key_sets(harness, variant):
    provider, state, first, _ = harness
    key = jwk(first)
    variants = {
        "duplicate": [key, key],
        "many": [{**key, "kid": str(i)} for i in range(33)],
        "wrong_type": [{**key, "kty": "oct"}],
        "wrong_alg": [{**key, "alg": "RS512"}],
        "wrong_use": [{**key, "use": "enc"}],
    }
    state["jwks"] = {"keys": variants[variant]}
    with pytest.raises(BffUnavailable):
        exchange(provider)


def test_logout_fixed_redirect_no_token(harness):
    provider, state, _, _ = harness
    exchange(provider)
    url = provider.logout_url()
    assert parse_qs(urlsplit(url).query) == {
        "client_id": [CLIENT_ID],
        "post_logout_redirect_uri": ["https://app.example/"],
    }
    assert (
        "id_token_hint" not in url and SECRET not in url and state["token"] not in url
    )
    provider.close()
    assert provider.logout_url()


@pytest.mark.parametrize(
    "issuer",
    [
        "http://identity.example",
        "https://user:password@identity.example",
        "https://identity.example#fragment",
        "https://identity.example?query=yes",
    ],
)
def test_invalid_configured_issuer(issuer):
    with pytest.raises(ValueError):
        OidcHttpProvider(
            issuer, CLIENT_ID, SECRET, "https://app.example/cb", "https://app.example/"
        )


def test_http_only_explicit_loopback():
    with pytest.raises(ValueError):
        OidcHttpProvider(
            "http://localhost:8080/realm",
            CLIENT_ID,
            SECRET,
            "https://app.example/cb",
            "https://app.example/",
        )
    provider = OidcHttpProvider(
        "http://localhost:8080/realm",
        CLIENT_ID,
        SECRET,
        "https://app.example/cb",
        "https://app.example/",
        allow_loopback_http=True,
    )
    provider.close()


@pytest.mark.parametrize("error", ["invalid_client", "invalid_request"])
def test_client_configuration_errors_are_unavailable(harness, error):
    provider, state, _, _ = harness
    state["status"]["token"] = 400
    state["raw"]["token"] = json.dumps(
        {"error": error, "error_description": SECRET}
    ).encode()
    with pytest.raises(BffUnavailable) as caught:
        exchange(provider)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("verifier", ["", "short", "x" * 129, "é" * 50])
def test_pkce_required_before_network(harness, verifier):
    provider, state, _, _ = harness
    with pytest.raises(BffUnauthenticated):
        provider.exchange("code", NONCE, verifier)
    assert not state["requests"]
