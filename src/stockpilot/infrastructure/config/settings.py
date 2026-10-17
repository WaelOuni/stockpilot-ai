from pydantic_settings import BaseSettings
from typing import Literal
import ipaddress
import re
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator


class Settings(BaseSettings):
    http_body_max_bytes: int = Field(default=128 * 1024, ge=128, le=1024 * 1024)
    http_body_timeout_seconds: float = Field(default=10.0, ge=0.1, le=60, allow_inf_nan=False)
    api_message_max_chars: int = Field(default=8000, ge=1, le=32000)
    api_thread_max_chars: int = Field(default=128, ge=1, le=256)
    api_identifier_max_chars: int = Field(default=256, ge=1, le=256)
    conversation_max_inflight: int = Field(default=4, ge=1, le=32)
    ollama_max_prompt_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    ollama_timeout_seconds: float = Field(default=60.0, gt=0, le=120, allow_inf_nan=False)
    http_trusted_hosts: list[str] = Field(default_factory=list)
    http_allowed_origins: list[str] = Field(default_factory=list)

    @field_validator("http_trusted_hosts")
    @classmethod
    def exact_http_hosts(cls, hosts: list[str]) -> list[str]:
        normalized = []
        for host in hosts:
            try:
                address = ipaddress.ip_address(host.removeprefix("[").removesuffix("]"))
                host = f"[{address}]" if address.version == 6 else str(address)
            except ValueError:
                if (not re.fullmatch(r"[a-zA-Z0-9.-]+", host)
                        or host.startswith(".") or host.endswith(".") or ".." in host):
                    raise ValueError("HTTP trusted hosts must be exact hostnames or IP addresses.") from None
            normalized.append(host)
        return normalized

    @field_validator("http_allowed_origins")
    @classmethod
    def exact_http_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.path or parsed.query
                    or parsed.fragment or "*" in origin or any(c.isspace() for c in origin)):
                raise ValueError("HTTP allowed origins must be exact HTTP(S) origins.")
            if parsed.port == 0:
                raise ValueError("HTTP allowed origins must use a valid port.")
        return origins

    product_import_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=16 * 1024 * 1024)
    product_import_max_products: int = Field(default=500, ge=1, le=500)
    admin_username: str | None = Field(default=None, repr=False)
    admin_password_verifier: SecretStr | None = Field(default=None, repr=False)
    admin_trusted_origin: str | None = None
    deployment_environment: Literal["development", "test", "local_demo", "production_saas", "production"] = "production_saas"
    admin_allow_local_http: bool = False
    admin_session_ttl_seconds: int = Field(default=1800, ge=1, le=86400)
    admin_max_sessions: int = Field(default=100, ge=1, le=1000)
    admin_session_store: Literal["memory", "mongo"] = "memory"
    backoffice_api_token: SecretStr | None = Field(default=None, repr=False)
    customer_jwt_issuer: str | None = None
    customer_jwt_audience: str | None = None
    customer_jwt_secret: SecretStr | None = Field(default=None, repr=False)
    otel_enabled: bool = False
    otel_service_name: str = "stockpilot-ai"
    otel_exporter: Literal["console", "otlp"] = "console"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_metric_export_interval_ms: int = Field(default=10000, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: str | None = "logs/stockpilot.jsonl"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    mongo_uri: str = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true"
    mongo_database: str = "stockpilot"
    langgraph_checkpoint_db_uri: str | None = None
    ollama_embedding_model: str = "nomic-embed-text"
    postgres_url: str = (
        "postgresql://stockpilot:stockpilot@localhost:5432/stockpilot"
    )
