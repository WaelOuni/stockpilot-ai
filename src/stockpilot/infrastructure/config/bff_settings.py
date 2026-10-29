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
    oidc_ca_file: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = Field(default=None, repr=False)
    mongo_uri: SecretStr | None = Field(default=None, repr=False)
    mongo_database: str | None = None
    allow_loopback_http_issuer: bool = False
    session_absolute_seconds: int = Field(default=1800, ge=30, le=3600)
    session_idle_seconds: int = Field(default=600, ge=10, le=1800)
    transaction_seconds: int = Field(default=300, ge=30, le=300)
    configuration_key: SecretStr | None = Field(default=None, repr=False)

    refresh_enabled: bool = False
    refresh_encryption_key: SecretStr | None = Field(default=None, repr=False)

    assistant_enabled: bool = False
    assistant_postgres_uri: SecretStr | None = Field(default=None, repr=False)
    assistant_schema: str = "stockpilot_b2b"
    assistant_ollama_url: str | None = None
    assistant_allow_http: bool = False
    assistant_model: str = "qwen2.5:7b"
    assistant_embedding_model: str = "nomic-embed-text"
    assistant_timeout_seconds: float = Field(default=60, ge=1, le=90)

    def validate_assistant(self):
        from psycopg.conninfo import conninfo_to_dict

        from stockpilot.infrastructure.persistence.postgres.tenant_migrations import (
            schema_name,
        )

        try:
            if self.mode != "b2b_business_test" or not self.assistant_postgres_uri:
                raise ValueError
            info = conninfo_to_dict(self.assistant_postgres_uri.get_secret_value())
            if (
                not re.fullmatch(
                    r"stockpilot_(ci|test)(_[a-z0-9]+)*", info.get("dbname", "")
                )
                or int(info.get("port", 5432)) == 5432
            ):
                raise ValueError
            schema_name(self.assistant_schema)
            endpoint = urlsplit(self.assistant_ollama_url or "")
            if (
                endpoint.scheme != "https"
                and not (self.assistant_allow_http and endpoint.scheme == "http")
            ) or not endpoint.hostname:
                raise ValueError
            if (
                endpoint.path not in {"", "/"}
                or endpoint.query
                or endpoint.fragment
                or endpoint.username
                or endpoint.password
            ):
                raise ValueError
            if (
                not self.assistant_model.strip()
                or not self.assistant_embedding_model.strip()
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise BffUnavailable(
                "Assistant configuration incomplete or unsafe."
            ) from None

    @property
    def callback_uri(self) -> str:
        return f"{self.origin}/b2b/auth/callback"

    @property
    def post_logout_uri(self) -> str:
        return f"{self.origin}/b2b/auth/logged-out"

    def validated_policy(self) -> BffPolicy:
        try:
            import ssl

            if self.oidc_ca_file:
                ssl.create_default_context(cafile=self.oidc_ca_file)
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
            if self.refresh_enabled:
                from stockpilot.infrastructure.identity.token_protector import AesTokenProtector
                if not self.refresh_encryption_key:
                    raise ValueError
                AesTokenProtector(self.refresh_encryption_key.get_secret_value())
            if self.session_idle_seconds > self.session_absolute_seconds:
                raise ValueError
        except (ValueError, TypeError, OSError):
            raise BffUnavailable("BFF configuration incomplete or unsafe.") from None
        if self.assistant_enabled:
            self.validate_assistant()
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
        if self.oidc_ca_file:
            from pathlib import Path

            context += sha256(Path(self.oidc_ca_file).read_bytes()).hexdigest()
        if self.mode == "b2b_business_test":
            context += json.dumps([self.mode, self.trusted_proxy_ips])
        if self.assistant_enabled:
            context += json.dumps(
                [
                    self.assistant_postgres_uri.get_secret_value(),
                    self.assistant_schema,
                    self.assistant_ollama_url,
                    self.assistant_model,
                    self.assistant_embedding_model,
                ]
            )
        if self.refresh_enabled:
            context += json.dumps(["server-refresh-v1", sha256(self.refresh_encryption_key.get_secret_value().encode()).hexdigest()])
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
