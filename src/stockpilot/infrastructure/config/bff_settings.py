"""Separate opt-in BFF test entrypoint; never opens the historical business app."""

import json
import re
from hashlib import sha256
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from stockpilot.application.ports.bff_security import BffUnavailable
from stockpilot.application.services.bff_sessions import BffPolicy


class BffSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BFF_", env_file=None, extra="ignore")
    mode: Literal["disabled", "b2b_auth_test", "b2b_business_test"] = "disabled"
    max_inflight: int = Field(default=8, ge=1, le=32)
    requests_per_minute: int = Field(default=600, ge=1, le=10000)
    body_timeout_seconds: float = Field(default=5, gt=0, le=10)
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    origin: str | None = None
    issuer: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = Field(default=None, repr=False)
    mongo_uri: SecretStr | None = Field(default=None, repr=False)
    mongo_database: str | None = None
    allow_loopback_http_issuer: bool = False
    session_absolute_seconds: int = Field(default=1800, ge=30, le=3600)
    session_idle_seconds: int = Field(default=600, ge=10, le=1800)
    transaction_seconds: int = Field(default=300, ge=30, le=300)
    configuration_key: SecretStr | None = Field(default=None, repr=False)

    @property
    def callback_uri(self) -> str:
        return f"{self.origin}/b2b/auth/callback"

    @property
    def post_logout_uri(self) -> str:
        return f"{self.origin}/b2b/auth/logged-out"

    def validated_policy(self) -> BffPolicy:
        try:
            origin = urlsplit(self.origin or "")
            issuer = urlsplit(self.issuer or "")
            if (
                origin.scheme != "https"
                or not origin.hostname
                or origin.path
                or origin.query
                or origin.fragment
                or origin.username
                or origin.password
                or origin.port == 0
                or "*" in self.origin
                or any(c.isspace() for c in self.origin)
            ):
                raise ValueError
            local_issuer = (
                self.allow_loopback_http_issuer
                and issuer.scheme == "http"
                and issuer.hostname in {"127.0.0.1", "localhost", "::1"}
            )
            if (issuer.scheme != "https" and not local_issuer) or not issuer.hostname:
                raise ValueError
            if (
                issuer.query
                or issuer.fragment
                or issuer.username
                or issuer.password
                or "*" in self.issuer
                or self.issuer.endswith("/")
                or any(c.isspace() for c in self.issuer)
            ):
                raise ValueError
            if (
                not self.client_id
                or not self.client_id.strip()
                or len(self.client_id) > 128
            ):
                raise ValueError
            if (
                not self.client_secret
                or len(self.client_secret.get_secret_value()) < 32
            ):
                raise ValueError
            if (
                not self.configuration_key
                or len(self.configuration_key.get_secret_value()) < 32
            ):
                raise ValueError
            if not self.mongo_uri or not self.mongo_database:
                raise ValueError
            if not re.fullmatch(
                r"stockpilot_b2b_[A-Za-z0-9_]{1,48}", self.mongo_database
            ):
                raise ValueError
            import ipaddress

            for peer in self.trusted_proxy_ips:
                ipaddress.ip_address(peer)  # Exact peers only, no wildcard/CIDR.
            if self.mode == "b2b_business_test" and not re.fullmatch(
                r"stockpilot_b2b_test_[0-9a-f]{32}", self.mongo_database
            ):
                raise ValueError
            if self.session_idle_seconds > self.session_absolute_seconds:
                raise ValueError
        except (ValueError, TypeError):
            raise BffUnavailable("BFF configuration incomplete or unsafe.") from None
        # HMAC prevents a DB-only reader guessing configuration values. Rotation
        # intentionally invalidates transactions and sessions across all workers.
        import hmac

        context = json.dumps(
            [
                self.origin,
                self.issuer,
                self.client_id,
                self.client_secret.get_secret_value(),
                self.mongo_database,
                self.session_absolute_seconds,
                self.session_idle_seconds,
                self.transaction_seconds,
            ],
            separators=(",", ":"),
        )
        if self.mode == "b2b_business_test":
            context += json.dumps([self.mode, self.trusted_proxy_ips])
        fingerprint = hmac.new(
            self.configuration_key.get_secret_value().encode(),
            context.encode(),
            sha256,
        ).hexdigest()
        return BffPolicy(
            fingerprint,
            self.transaction_seconds,
            self.session_absolute_seconds,
            self.session_idle_seconds,
        )
