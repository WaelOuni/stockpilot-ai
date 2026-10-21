"""Opt-in and strict public contract, without databases or providers."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from stockpilot.application.ports.bff_security import BffUnavailable
from stockpilot.bff import create_app
from stockpilot.infrastructure.config.bff_settings import BffSettings
from stockpilot.infrastructure.persistence.postgres.tenant_migrations import registry
from stockpilot.presentation.api.routes.b2b_assistant import (
    ActionIn,
    CancelIn,
    MessageIn,
    public_turn,
)


@pytest.mark.parametrize("mode", ["disabled", "b2b_auth_test", "b2b_business_test"])
def test_no_assistant_route_enabled_by_default(mode):
    app = create_app(BffSettings(mode=mode))
    assert not any("/assistant/" in path for path in app.openapi()["paths"])
    with TestClient(app, base_url="https://invalid.example") as client:
        assert client.get("/b2b/api/assistant/conversations").status_code != 200


@pytest.mark.parametrize("value", [1, "true", False, None])
def test_explicit_boolean_cannot_be_coerced(value):
    with pytest.raises(ValidationError):
        CancelIn(operation_id="op", order_id="order", confirmed=value)
    with pytest.raises(ValidationError):
        ActionIn(
            operation_id="op",
            order_id="order",
            preparation_digest="digest",
            confirmed=value,
        )


@pytest.mark.parametrize(
    "extra",
    [{"organization_id": "foreign"}, {"role": "MANAGER"}, {"permissions": ["*"]}],
)
def test_identity_fields_not_accepted(extra):
    with pytest.raises(ValidationError):
        MessageIn(message_id="id", message="question", **extra)


def test_private_state_and_action_are_not_serialized():
    turn = {
        "turn_id": "id",
        "kind": "message",
        "message": "question",
        "status": "completed",
        "created_at": "2026-10-21T00:00:00Z",
        "operation": None,
        "action": {"session": "private"},
        "reply": {
            "intent": "RAG",
            "text": "answer",
            "classification": {"internal": "private"},
            "prompt": "private",
            "products": [],
        },
    }
    result = public_turn(turn)
    assert "private" not in str(result) and "classification" not in result["reply"]


def test_postgres_migrations_are_separate_and_immutable():
    rows = registry()
    assert [row[0] for row in rows] == [1, 2]
    assert "assistant_turns" not in rows[0][2] and "assistant_turns" in rows[1][2]


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"mode": "b2b_auth_test"},
        {"assistant_postgres_uri": "postgresql://localhost:5432/stockpilot_test"},
        {"assistant_postgres_uri": "postgresql://localhost:57918/stockpilot"},
        {
            "assistant_ollama_url": "http://localhost:11434",
            "assistant_allow_http": False,
        },
        {"assistant_ollama_url": "https://host.example/path"},
    ],
)
def test_assistant_incomplete_or_protected_configuration_refused(changes):
    values = {
        "mode": "b2b_business_test",
        "assistant_postgres_uri": "postgresql://localhost:57918/stockpilot_ci",
        "assistant_ollama_url": "https://models.example",
    }
    if not changes:
        values.pop("assistant_postgres_uri")
    values.update(changes)
    with pytest.raises(BffUnavailable):
        BffSettings(**values).validate_assistant()
