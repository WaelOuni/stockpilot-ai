from pydantic_settings import BaseSettings
from typing import Literal
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    backoffice_api_token: SecretStr | None = Field(default=None, repr=False)
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