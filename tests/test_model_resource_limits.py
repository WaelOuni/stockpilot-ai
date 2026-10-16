import asyncio
import json

import httpx
import pytest

from stockpilot.application.resource_errors import ModelInputLimitError, ModelTimeoutError, ModelUnavailableError
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import OllamaEmbeddingAdapter
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
from stockpilot.infrastructure.llm import resource_limits


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    observed = []

    def client(**kwargs):
        observed.append(kwargs)
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(resource_limits.httpx, "AsyncClient", client)
    return observed


@pytest.mark.parametrize("adapter,method", [(OllamaAdapter, "generate"), (OllamaAdapter, "classify_intent"), (OllamaEmbeddingAdapter, "embed")])
def test_oversized_model_input_never_opens_http_client(monkeypatch, adapter, method):
    def unavailable(**kwargs):
        pytest.fail("oversized model input opened a network client")

    monkeypatch.setattr(resource_limits.httpx, "AsyncClient", unavailable)
    settings = Settings(ollama_max_prompt_bytes=1024)
    with pytest.raises(ModelInputLimitError):
        asyncio.run(getattr(adapter(settings), method)("é" * 1024))


@pytest.mark.parametrize("error,expected", [
    (httpx.ReadTimeout, ModelTimeoutError), (httpx.ConnectTimeout, ModelTimeoutError),
    (httpx.ConnectError, ModelUnavailableError),
])
def test_provider_transport_failures_are_typed_and_safe(monkeypatch, error, expected):
    def handler(request):
        raise error("private-secret-provider-address", request=request)

    transport(monkeypatch, handler)
    with pytest.raises(expected) as result:
        asyncio.run(OllamaAdapter(Settings()).generate("hello"))
    assert "private" not in str(result.value)
    assert result.value.__suppress_context__


@pytest.mark.parametrize("status,body", [(503, {"error": "private"}), (200, None)])
def test_unavailable_or_malformed_provider_response_is_safe(monkeypatch, status, body):
    transport(monkeypatch, lambda request: httpx.Response(status, json=body) if body is not None
              else httpx.Response(200, content=b"private-invalid-json"))
    with pytest.raises(ModelUnavailableError):
        asyncio.run(OllamaAdapter(Settings()).generate("hello"))


def test_generation_payload_and_per_operation_timeout_are_preserved(monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"response": "answer"})

    observed = transport(monkeypatch, handler)
    settings = Settings(ollama_timeout_seconds=2.5)
    assert asyncio.run(OllamaAdapter(settings).generate("unchanged prompt")) == "answer"
    assert observed == [{"timeout": 2.5}]
    assert requests == [{"model": settings.ollama_model, "prompt": "unchanged prompt", "stream": False}]
