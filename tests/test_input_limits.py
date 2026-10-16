import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from customer_auth import configure_customer_auth, customer_headers
from stockpilot.application.resource_errors import ModelTimeoutError, ModelUnavailableError
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.main import app
from stockpilot.presentation.api.input_protection import InputProtectionMiddleware


@pytest.fixture(autouse=True)
def controlled_api(monkeypatch):
    configure_customer_auth(monkeypatch, app)
    workflow = SimpleNamespace(ainvoke=AsyncMock(return_value={"response": "ok"}))
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    return workflow


def limits(monkeypatch, **changes):
    monkeypatch.setattr(app.state, "settings", app.state.settings.model_copy(update=changes))


@pytest.mark.parametrize("path", ["/assistant", "/chat", "/classify"])
def test_overlong_messages_never_reach_workflow_or_model(monkeypatch, controlled_api, path):
    limits(monkeypatch, api_message_max_chars=20)
    generation = SimpleNamespace(execute=AsyncMock())
    classification = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(app.state, "generate_response_use_case", generation)
    monkeypatch.setattr(app.state, "classify_intent_use_case", classification)
    payload = {"message": "x" * 21, **({"thread_id": "t"} if path == "/assistant" else {})}
    response = TestClient(app).post(path, headers=customer_headers(), json=payload)
    assert response.status_code == 422
    controlled_api.ainvoke.assert_not_awaited()
    generation.execute.assert_not_awaited()
    classification.execute.assert_not_awaited()


def test_message_boundary_and_thread_limit(monkeypatch, controlled_api):
    limits(monkeypatch, api_message_max_chars=20, api_thread_max_chars=3)
    client = TestClient(app)
    assert client.post("/assistant", headers=customer_headers(), json={
        "message": "x" * 20, "thread_id": "abc",
    }).status_code == 200
    controlled_api.ainvoke.reset_mock()
    assert client.post("/assistant", headers=customer_headers(), json={
        "message": "oui", "thread_id": "abcd",
    }).status_code == 422
    controlled_api.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("extra", [
    {"history": [{"role": "system", "content": "private-value"}] * 100},
    {"metadata": {"tenant_id": "private-value"}},
    {"tenant_id": "private-value"},
    {"private-field-name": "private-value"},
])
def test_unknown_fields_and_client_history_rejected_safely(controlled_api, extra):
    response = TestClient(app).post("/assistant", headers=customer_headers(), json={
        "message": "private-message", "thread_id": "private-thread", **extra,
    })
    assert response.status_code == 422
    assert "private" not in response.text
    controlled_api.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    {"message": {"secret": "private-value"}, "thread_id": "private-thread"},
    {"message": "private-value", "thread_id": 42},
    {"message": "", "thread_id": "private-value"},
])
def test_invalid_payload_does_not_echo_submitted_input(controlled_api, payload):
    response = TestClient(app).post("/assistant", headers=customer_headers(), json=payload)
    assert response.status_code == 422
    assert "private" not in response.text
    controlled_api.ainvoke.assert_not_awaited()


def test_large_body_rejected_before_dispatch(monkeypatch, controlled_api):
    limits(monkeypatch, http_body_max_bytes=128)
    response = TestClient(app).post("/assistant", headers=customer_headers(), json={
        "message": "x" * 129, "thread_id": "t",
    })
    assert response.status_code == 413
    controlled_api.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_streamed_bytes_are_bounded_even_without_honest_content_length(headers):
    entered = False
    sent = []
    frames = iter([
        {"type": "http.request", "body": b"a" * 100, "more_body": True},
        {"type": "http.request", "body": b"b" * 100, "more_body": False},
    ])

    async def inner(scope, receive, send):
        nonlocal entered
        entered = True

    async def receive():
        return next(frames)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": "/assistant", "method": "POST", "headers": headers,
             "app": SimpleNamespace(state=SimpleNamespace(settings=Settings(http_body_max_bytes=128)))}
    asyncio.run(InputProtectionMiddleware(inner)(scope, receive, send))
    assert entered is False
    assert sent[0]["status"] == 413


def test_path_identifiers_are_bounded_before_repository(monkeypatch):
    limits(monkeypatch, api_identifier_max_chars=8)
    use_case = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(app.state, "get_customer_order_use_case", use_case)
    response = TestClient(app).get("/orders/" + "x" * 9, headers=customer_headers())
    assert response.status_code == 422
    use_case.execute.assert_not_awaited()


@pytest.mark.parametrize("failure,status", [(None, 200), (ModelTimeoutError, 504), (ModelUnavailableError, 503)])
def test_immediate_admission_rejects_extra_request_and_releases_on_all_outcomes(monkeypatch, failure, status):
    limits(monkeypatch, conversation_max_inflight=1)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def invoke(*args, **kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            if failure is not None:
                raise failure("private-provider-url-and-secret")
            return {"response": "ok"}

        workflow = SimpleNamespace(ainvoke=AsyncMock(side_effect=invoke))
        monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://limits-test") as client:
            payload = {"message": "oui confirme", "thread_id": "admission"}
            first = asyncio.create_task(client.post("/assistant", json=payload, headers=customer_headers()))
            await asyncio.wait_for(entered.wait(), timeout=2)
            rejected = await client.post("/assistant", json=payload, headers=customer_headers())
            assert rejected.status_code == 429
            assert rejected.headers["Retry-After"] == "1"
            assert calls == 1
            release.set()
            response = await first
            assert response.status_code == status
            assert "private" not in response.text
            retry = await client.post("/assistant", json=payload, headers=customer_headers())
            assert retry.status_code == status
            assert calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("configuration", [
    {"api_message_max_chars": 32001}, {"api_thread_max_chars": 257},
    {"api_identifier_max_chars": 257}, {"http_body_max_bytes": 1024 * 1024 + 1},
    {"conversation_max_inflight": 0}, {"ollama_timeout_seconds": float("inf")},
])
def test_invalid_limit_configuration_is_rejected(configuration):
    with pytest.raises(ValidationError):
        Settings(**configuration)
