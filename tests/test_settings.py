from stockpilot.infrastructure.config.settings import Settings


def test_settings_use_local_defaults(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.delenv("LANGGRAPH_CHECKPOINT_DB_URI", raising=False)

    settings = Settings(_env_file=None)

    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_model == "qwen2.5:7b"
    assert settings.mongo_uri == (
        "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true"
    )
    assert settings.langgraph_checkpoint_db_uri is None


def test_settings_can_be_overridden_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.example")
    monkeypatch.setenv("OLLAMA_MODEL", "custom-model")
    monkeypatch.setenv("MONGO_URI", "mongodb://mongo.example:27017/?replicaSet=custom")

    settings = Settings(_env_file=None)

    assert settings.ollama_base_url == "https://ollama.example"
    assert settings.ollama_model == "custom-model"
    assert settings.mongo_uri == "mongodb://mongo.example:27017/?replicaSet=custom"


def test_checkpoint_uri_is_independently_overrideable(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DB_URI", "postgresql://localhost/checkpoints")
    settings = Settings(_env_file=None)
    assert settings.langgraph_checkpoint_db_uri == "postgresql://localhost/checkpoints"


def test_log_level_can_be_overridden(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert Settings(_env_file=None).log_level == "DEBUG"
