from pydantic_settings import BaseSettings
from typing import Literal
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    product_import_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=16 * 1024 * 1024)
    product_import_max_products: int = Field(default=500, ge=1, le=500)
    admin_username: str | None = Field(default=None, repr=False)
    admin_password_verifier: SecretStr | None = Field(default=None, repr=False)
    admin_trusted_origin: str | None = None
    deployment_environment: Literal["development", "production"] = "development"
    admin_allow_local_http: bool = False
    admin_session_ttl_seconds: int = Field(default=1800, ge=1, le=86400)
    admin_max_sessions: int = Field(default=100, ge=1, le=1000)
    admin_session_store: Literal["memory", "mongo"] = "memory"
    backoffice_api_token: SecretStr | None = Field(default=None, repr=False)
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
