"""Confidential OIDC code flow; provider tokens never cross this boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import ssl
import threading
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from stockpilot.application.ports.bff_security import (
    AuthenticatedIdentity,
    BffUnauthenticated,
    BffUnavailable,
)
from stockpilot.domain.entities.identity import ExternalIdentity

_MAX_RESPONSE = 131_072
_MAX_KEYS = 32
_CACHE_SECONDS = 300.0
_REFRESH_COOLDOWN = 10.0
_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


class OidcHttpProvider:
    """Bounded synchronous OIDC adapter with an injectable HTTP transport."""

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        post_logout_redirect_uri: str,
        *,
        allow_loopback_http: bool = False,
        client: httpx.Client | None = None,
        ca_file: str | None = None,
    ):
        self._allow_loopback_http = allow_loopback_http
        self._issuer = issuer
        self._origin = self._checked_origin(issuer)
        if urlsplit(issuer).query or not client_id or not client_secret:
            raise ValueError("Invalid OIDC configuration")
        self._checked_origin(redirect_uri)
        self._checked_origin(post_logout_redirect_uri)
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._post_logout_redirect_uri = post_logout_redirect_uri
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.Client(
                trust_env=False, verify=ssl.create_default_context(cafile=ca_file)
            )
        )
        self._lock = threading.RLock()
        self._metadata: dict[str, Any] | None = None
        self._metadata_until = 0.0
        self._metadata_attempt = float("-inf")
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_until = 0.0
        self._keys_attempt = float("-inf")
        self._jwks_uri: str | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _checked_origin(self, value: str) -> tuple[str, str, int]:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            if (
                not host
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            ):
                raise ValueError("Invalid OIDC URL")
            loopback = host.lower() in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and self._allow_loopback_http and loopback
            ):
                raise ValueError("OIDC requires HTTPS")
            return (
                parsed.scheme,
                host.lower(),
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
        except (TypeError, AttributeError, ValueError):
            raise ValueError("Invalid OIDC URL") from None

    def _endpoint(self, value: Any) -> str:
        try:
            if (
                not isinstance(value, str)
                or self._checked_origin(value) != self._origin
            ):
                raise ValueError("Untrusted endpoint")
        except ValueError:
            raise BffUnavailable(
                "Identity provider configuration unavailable"
            ) from None
        return value

    def _json(
        self, method: str, url: str, *, token: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            with self._client.stream(
                method, url, timeout=_TIMEOUT, follow_redirects=False, **kwargs
            ) as response:
                if response.status_code != 200 and not (
                    token and response.status_code == 400
                ):
                    raise BffUnavailable("Identity provider unavailable")
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > _MAX_RESPONSE:
                        raise BffUnavailable("Identity provider response invalid")
                    chunks.extend(chunk)
                result = json.loads(chunks)
                if not isinstance(result, dict):
                    raise BffUnavailable("Identity provider response invalid")
                if response.status_code == 400:
                    if result.get("error") == "invalid_grant":
                        raise BffUnauthenticated(
                            "Identity provider rejected authentication"
                        )
                    raise BffUnavailable(
                        "Identity provider client configuration unavailable"
                    )
                return result
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise BffUnavailable("Identity provider unavailable") from None

    def _discovery(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if self._metadata is not None and now < self._metadata_until:
                return self._metadata
            if now - self._metadata_attempt < _REFRESH_COOLDOWN:
                raise BffUnavailable("Identity provider discovery unavailable")
            self._metadata_attempt = now
            doc = self._json(
                "GET", self._issuer.rstrip("/") + "/.well-known/openid-configuration"
            )
            if doc.get("issuer") != self._issuer:
                raise BffUnavailable("Identity provider issuer mismatch")
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                doc[field] = self._endpoint(doc.get(field))
            if doc.get("end_session_endpoint") is not None:
                doc["end_session_endpoint"] = self._endpoint(
                    doc["end_session_endpoint"]
                )
            if self._jwks_uri is not None and self._jwks_uri != doc["jwks_uri"]:
                self._keys = {}
                self._keys_until = 0.0
            self._jwks_uri = doc["jwks_uri"]
            self._metadata = doc
            self._metadata_until = now + _CACHE_SECONDS
            return doc

    @staticmethod
    def _flow_inputs(nonce: str, verifier: str) -> None:
        if (
            not isinstance(nonce, str)
            or not nonce
            or not isinstance(verifier, str)
            or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
        ):
            raise BffUnauthenticated("Invalid OIDC transaction")

    def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        self._flow_inputs(nonce, verifier)
        if not isinstance(state, str) or not state:
            raise BffUnauthenticated("Invalid OIDC transaction")
        endpoint = self._discovery()["authorization_endpoint"]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        params = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": "openid",
                # A new StockPilot login must identify the intended person, not
                # silently reuse a different account's surviving SSO session.
                "prompt": "login",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return endpoint + ("&" if urlsplit(endpoint).query else "?") + params

    def _refresh_keys(self, uri: str) -> None:
        now = time.monotonic()
        if now - self._keys_attempt < _REFRESH_COOLDOWN:
            return
        self._keys_attempt = now
        doc = self._json("GET", uri)
        raw_keys = doc.get("keys")
        if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= _MAX_KEYS:
            raise BffUnavailable("Identity provider keys invalid")
        keys: dict[str, jwt.PyJWK] = {}
        seen: set[str] = set()
        for raw in raw_keys:
            if not isinstance(raw, dict):
                raise BffUnavailable("Identity provider keys invalid")
            kid = raw.get("kid")
            if not isinstance(kid, str) or not kid or len(kid) > 256 or kid in seen:
                raise BffUnavailable("Identity provider key identifiers invalid")
            seen.add(kid)
            if (
                raw.get("kty") != "RSA"
                or raw.get("alg", "RS256") != "RS256"
                or raw.get("use", "sig") != "sig"
            ):
                continue
            if "key_ops" in raw and (
                not isinstance(raw["key_ops"], list) or "verify" not in raw["key_ops"]
            ):
                continue
            if any(name in raw for name in ("d", "p", "q", "dp", "dq", "qi")):
                raise BffUnavailable("Identity provider keys invalid")
            try:
                key = jwt.PyJWK.from_dict(raw, algorithm="RS256")
                if getattr(key.key, "key_size", 0) < 2048:
                    raise ValueError("Weak RSA key")
            except (jwt.PyJWTError, ValueError, TypeError, KeyError):
                raise BffUnavailable("Identity provider keys invalid") from None
            keys[kid] = key
        if not keys:
            raise BffUnavailable("Identity provider signing keys unavailable")
        self._keys = keys
        self._keys_until = now + _CACHE_SECONDS

    def _key(self, kid: str, uri: str) -> jwt.PyJWK:
        with self._lock:
            if time.monotonic() >= self._keys_until or kid not in self._keys:
                self._refresh_keys(uri)
            if time.monotonic() >= self._keys_until:
                raise BffUnavailable("Identity provider keys unavailable")
            key = self._keys.get(kid)
            if key is None:
                raise BffUnauthenticated("Identity token signing key unknown")
            return key

    @staticmethod
    def _numeric_date(value: Any) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    def exchange(self, code: str, nonce: str, verifier: str) -> AuthenticatedIdentity:
        self._flow_inputs(nonce, verifier)
        doc = self._discovery()
        response = self._json(
            "POST",
            doc["token_endpoint"],
            token=True,
            auth=httpx.BasicAuth(self._client_id, self._client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "code_verifier": verifier,
            },
        )
        encoded = response.get("id_token")
        # Access/refresh tokens are never read, returned or persisted.
        response.clear()
        if not isinstance(encoded, str) or not encoded or len(encoded) > 32_768:
            raise BffUnauthenticated("Identity token missing or invalid")
        try:
            header = jwt.get_unverified_header(encoded)
        except jwt.PyJWTError:
            raise BffUnauthenticated("Identity token invalid") from None
        kid = header.get("kid")
        if (
            header.get("alg") != "RS256"
            or not isinstance(kid, str)
            or not kid
            or len(kid) > 256
            or header.get("crit")
        ):
            raise BffUnauthenticated("Identity token header invalid")
        key = self._key(kid, doc["jwks_uri"])
        try:
            claims = jwt.decode(
                encoded,
                key.key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._client_id,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"]},
            )
            if not self._numeric_date(claims["exp"]) or not self._numeric_date(
                claims["iat"]
            ):
                raise ValueError("Invalid time claims")
            if "nbf" in claims and not self._numeric_date(claims["nbf"]):
                raise ValueError("Invalid time claims")
            if claims["exp"] <= claims["iat"]:
                raise ValueError("Invalid lifetime")
            sub = claims["sub"]
            if not isinstance(sub, str) or not sub.strip():
                raise ValueError("Invalid subject")
            actual_nonce = claims["nonce"]
            if (
                not isinstance(actual_nonce, str)
                or not actual_nonce
                or not hmac.compare_digest(
                    actual_nonce.encode("utf-8"), nonce.encode("utf-8")
                )
            ):
                raise ValueError("Invalid nonce")
            audiences = claims["aud"]
            if isinstance(audiences, list) and (
                not audiences or any(not isinstance(a, str) or not a for a in audiences)
            ):
                raise ValueError("Invalid audience")
            if (
                (isinstance(audiences, list) and len(audiences) > 1) or "azp" in claims
            ) and claims.get("azp") != self._client_id:
                raise ValueError("Invalid authorized party")
            expires_at = datetime.fromtimestamp(claims["exp"], tz=UTC)
            identity = ExternalIdentity(issuer=self._issuer, subject=sub)
        except (jwt.PyJWTError, ValueError, TypeError, OverflowError, OSError):
            raise BffUnauthenticated("Identity token invalid") from None
        return AuthenticatedIdentity(identity=identity, expires_at=expires_at)

    def logout_url(self) -> str:
        endpoint = self._discovery().get("end_session_endpoint")
        if endpoint is None:
            raise BffUnavailable("Identity provider logout unavailable")
        return (
            endpoint
            + ("&" if urlsplit(endpoint).query else "?")
            + urlencode(
                {
                    "client_id": self._client_id,
                    "post_logout_redirect_uri": self._post_logout_redirect_uri,
                }
            )
        )
