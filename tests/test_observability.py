import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi.testclient import TestClient

from stockpilot.main import app
from stockpilot.observability import JsonFormatter, event, log_context, observe_llm
import pytest
from stockpilot.application.workflows.nodes import create_order_confirmation_node
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus


def records(caplog, name):
    return [record for record in caplog.records if getattr(record, "event", None) == name]


def test_assistant_logs_correlation_without_user_content(monkeypatch, caplog):
    secret = "private-user-message-should-not-appear"
    result = {
        "intent": "ORDER", "order_status": "CONFIRMED", "created_order_id": "order-1",
        "pending_order": {"operation_id": "op-1"}, "response": "private-response",
    }
    monkeypatch.setattr(app.state, "stockpilot_workflow",
                        SimpleNamespace(ainvoke=AsyncMock(return_value=result)))
    with caplog.at_level(logging.INFO):
        response = TestClient(app).post(
            "/assistant", json={"message": secret, "thread_id": "thread-1"}
        )
    request_id = response.headers["X-Request-ID"]
    UUID(request_id)
    completed = records(caplog, "assistant_request_completed")[0]
    assert completed.fields["thread_id"] == "thread-1"
    assert completed.fields["request_id"] == request_id
    assert completed.fields["operation_id"] == "op-1"
    assert completed.fields["order_id"] == "order-1"
    assert completed.fields["duration_ms"] >= 0
    rendered = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert secret not in rendered
    assert "private-response" not in rendered


def test_failed_assistant_returns_generic_error_with_request_id(monkeypatch, caplog):
    secret = "secret-internal-error"
    monkeypatch.setattr(app.state, "stockpilot_workflow",
                        SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError(secret))))
    with caplog.at_level(logging.INFO):
        response = TestClient(app).post(
            "/assistant", json={"message": "private-message", "thread_id": "failure-thread"}
        )
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    UUID(response.headers["X-Request-ID"])
    failed = records(caplog, "assistant_request_failed")[0]
    assert failed.exc_info
    assert failed.fields["thread_id"] == "failure-thread"
    rendered = JsonFormatter().format(failed)
    assert secret not in rendered
    assert json.loads(rendered)["error_type"] == "RuntimeError"


def test_request_ids_differ_and_context_does_not_leak(monkeypatch, caplog):
    monkeypatch.setattr(app.state, "stockpilot_workflow",
                        SimpleNamespace(ainvoke=AsyncMock(return_value={})))
    with caplog.at_level(logging.INFO):
        client = TestClient(app)
        first = client.post("/assistant", json={"message": "x", "thread_id": "thread-a"})
        second = client.get("/health")
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    last = records(caplog, "http_request_completed")[-1]
    assert "thread_id" not in last.fields
    assert "operation_id" not in last.fields


def test_order_finalized_logs_saved_identifiers(caplog):
    order = Order(order_id="order-2", operation_id="op-2", product_name="Bosch X100",
                  quantity=2, status=OrderStatus.CREATED,
                  created_at=datetime.now(timezone.utc))
    finalize = SimpleNamespace(execute=AsyncMock(return_value=order))

    async def run():
        with log_context(request_id="request-2", thread_id="thread-2"):
            return await create_order_confirmation_node(finalize)({
                "message": "Oui, je confirme", "order_status": "PENDING_CONFIRMATION",
                "pending_order": {"operation_id": "op-2", "product_name": "Bosch X100",
                                  "quantity": 2, "status": "PENDING_CONFIRMATION"},
            })
    with caplog.at_level(logging.INFO):
        asyncio.run(run())
    finalized = records(caplog, "order_finalized")[0]
    assert finalized.fields["operation_id"] == "op-2"
    assert finalized.fields["order_id"] == "order-2"
    assert finalized.fields["thread_id"] == "thread-2"
    assert finalized.fields["duration_ms"] >= 0


def test_json_formatter_and_async_context_isolation(caplog):
    logger = logging.getLogger("stockpilot.test")

    async def task(thread):
        with log_context(thread_id=thread):
            await asyncio.sleep(0)
            event(logger, "sample", duration_ms=0)

    async def run():
        await asyncio.gather(task("a"), task("b"))

    with caplog.at_level(logging.INFO):
        asyncio.run(run())
    logs = [json.loads(JsonFormatter().format(record)) for record in records(caplog, "sample")]
    assert {log["thread_id"] for log in logs} == {"a", "b"}
    assert all(log["level"] == "INFO" and log["timestamp"] for log in logs)


def test_llm_timing_never_logs_prompts_or_outputs(caplog):
    class FakeLlm:
        _settings = SimpleNamespace(ollama_model="fake-model")

        @observe_llm("generation")
        async def generate(self, prompt):
            return "private-answer"

    with caplog.at_level(logging.INFO):
        assert asyncio.run(FakeLlm().generate("private-prompt")) == "private-answer"
    completed = records(caplog, "llm_generation_completed")[0]
    assert completed.fields["model"] == "fake-model"
    assert completed.fields["duration_ms"] >= 0
    rendered = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert "private-prompt" not in rendered
    assert "private-answer" not in rendered


def test_llm_failure_records_type_not_exception_content(caplog):
    class FakeLlm:
        _settings = SimpleNamespace(ollama_model="fake-model")

        @observe_llm("classification")
        async def classify(self, prompt):
            raise RuntimeError("private-prompt-in-error")

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        asyncio.run(FakeLlm().classify("private-input"))
    failed = records(caplog, "llm_call_failed")[0]
    assert failed.fields["error_type"] == "RuntimeError"
    assert "private-prompt-in-error" not in JsonFormatter().format(failed)
