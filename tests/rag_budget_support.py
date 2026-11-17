"""Synthetic captured provider responses; never production data or live models."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from pydantic import SecretStr

from stockpilot.infrastructure.llm import resource_limits

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/rag_dense_budget.json').read_text())
VECTOR = [1.0] + [0.0] * 767


def settings(uri='postgresql://unused/unused', schema='synthetic'):
    return SimpleNamespace(
        validate_assistant=Mock(), assistant_postgres_uri=SecretStr(uri),
        assistant_schema=schema, assistant_model='qwen2.5:7b',
        assistant_embedding_model='synthetic-768',
        assistant_ollama_url='http://synthetic.invalid:11434',
        assistant_timeout_seconds=60, assistant_read_budget_seconds=75,
    )


def wire(monkeypatch, generate):
    original = httpx.AsyncClient
    requests, timeouts = [], []

    def handle(request):
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        body = {'embeddings': [VECTOR]} if request.url.path == '/api/embed' else generate(payload)
        return httpx.Response(200, json=body)

    def client(**kwargs):
        timeouts.append(kwargs['timeout'])
        return original(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(resource_limits.httpx, 'AsyncClient', client)
    return requests, timeouts
