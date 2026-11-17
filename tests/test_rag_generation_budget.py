"""RAG budget at the real composition/HTTP boundary; no tokenizer simulation."""
import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from rag_budget_support import FIXTURE, VECTOR, settings, wire
from stockpilot.application.assistant_runtime import observation, phase
from stockpilot.application.resource_errors import ModelResponseDecodeError
from stockpilot.application.services.tenant_retrieval import RAG_EXCERPT_RESPONSE_FORMAT, TenantRetrieval
from stockpilot.application.services.tenant_rag_safety import RAG_INSUFFICIENT_EVIDENCE
from stockpilot.domain.entities.tenant_knowledge import TenantPassage
from stockpilot.infrastructure.identity import bff_assistant
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter


@pytest.fixture
def composed(monkeypatch):
    monkeypatch.setattr(bff_assistant.psycopg, 'connect', Mock(return_value=MagicMock()))
    monkeypatch.setattr(bff_assistant, 'PostgresTenantMigrations', Mock())
    monkeypatch.setattr(bff_assistant, 'PgTenantAssistant', Mock())
    monkeypatch.setattr(bff_assistant, 'PgTenantKnowledge', Mock())
    return bff_assistant.compose_assistant(settings(), Mock())


def test_documentary_composition_transmits_1024_and_verifies_captured_dense_excerpts(composed, monkeypatch):
    requests, timeouts = wire(monkeypatch, lambda p: FIXTURE['complete'] if p['options']['num_predict'] == 1024 else FIXTURE['truncated'])
    passages = [TenantPassage('org-a', p['document_id'], p['chunk_id'], p['content'], .1, 'generation-1') for p in FIXTURE['passages']]
    retrieval = composed.retrieval
    retrieval.repository.search.return_value = passages
    retrieval.repository.cited_passage.side_effect = lambda request, ctx, doc, gen, chunk: next(p for p in passages if p.document_id == doc and p.chunk_id == chunk)
    citations, consulted = [], []
    text = asyncio.run(retrieval.execute(object(), SimpleNamespace(organization_id='org-a'), 'Résume les documents.', citations=citations, consulted_sources=consulted))
    generated = next(p for path,p in requests if path == '/api/generate')
    assert generated['options'] == {'num_predict': 1024, 'temperature': 0}
    assert generated['format'] == RAG_EXCERPT_RESPONSE_FORMAT
    draft = json.loads(FIXTURE['complete']['response'])
    assert text == '\n'.join(f'[{x["source_id"]}] « {x["excerpt"]} »' for x in draft['excerpts'])
    assert citations == consulted == [TenantRetrieval._reference(p) for p in passages]
    assert timeouts == [60, 60] and composed.read_budget_seconds == 75


def test_other_generation_classification_and_embedding_payloads_unchanged(composed, monkeypatch):
    requests, _ = wire(monkeypatch, lambda p: {'response': '{"intent":"UNKNOWN"}' if 'format' in p else 'ordinary answer'})
    assert asyncio.run(composed.llm.generate('ordinary prompt')) == 'ordinary answer'
    assert asyncio.run(composed.llm.classify_intent('ordinary question')).intent.value == 'UNKNOWN'
    assert asyncio.run(composed.retrieval.embeddings.embed('synthetic')) == VECTOR
    assert [p['options'] for path,p in requests if path == '/api/generate'] == [{'num_predict':512,'temperature':0}]*2
    assert requests[-1] == ('/api/embed', {'model':'synthetic-768','input':'synthetic'})


@pytest.mark.parametrize('budget', [512, 1024])
def test_length_truncated_json_is_observable_failure_not_documentary_abstention(composed, monkeypatch, caplog, budget):
    wire(monkeypatch, lambda p: FIXTURE['truncated'])
    adapter = OllamaAdapter(composed.llm._settings, max_output_tokens=budget)
    caplog.set_level(logging.INFO)
    with observation() as observed:
        with pytest.raises(ModelResponseDecodeError):
            with phase('generation'):
                asyncio.run(adapter.generate('synthetic prompt', RAG_EXCERPT_RESPONSE_FORMAT))
    assert observed.failure == ('generation', 'response_decode_error')
    assert any(getattr(r, 'event', '') == 'llm_call_failed' for r in caplog.records)
    assert FIXTURE['truncated']['response'] not in caplog.text
    assert 'synthetic prompt' not in caplog.text


@pytest.mark.parametrize('reason', ['stop', 'length'])
def test_complete_json_at_boundary_is_still_valid_and_citations_remain_validator_owned(composed, monkeypatch, reason):
    wire(monkeypatch, lambda p: {**FIXTURE['complete'], 'done_reason':reason})
    assert asyncio.run(composed.retrieval.generator.generate('synthetic', RAG_EXCERPT_RESPONSE_FORMAT)) == FIXTURE['complete']['response']


def test_explicit_complete_abstention_remains_legitimate(composed, monkeypatch):
    wire(monkeypatch, lambda p: {'response':'{"answerable":false,"excerpts":[]}', 'done_reason':'stop'})
    p = FIXTURE['passages'][0]
    composed.retrieval.repository.search.return_value = [TenantPassage('org-a',p['document_id'],0,p['content'],.1,'generation-1')]
    assert asyncio.run(composed.retrieval.execute(object(),SimpleNamespace(organization_id='org-a'),'Document ?')) == RAG_INSUFFICIENT_EVIDENCE


def test_dense_fixture_is_exact_valid_evidence_only_after_complete_generation():
    with pytest.raises(json.JSONDecodeError): json.loads(FIXTURE['truncated']['response'])
    draft=json.loads(FIXTURE['complete']['response'])
    sources={p['source']:p['content'] for p in FIXTURE['passages']}
    assert FIXTURE['truncated']['eval_count']==512 < FIXTURE['complete']['eval_count']==676 <= 1024
    assert len(draft['excerpts'])==3
    assert all(x['excerpt'] in sources[x['source_id']] for x in draft['excerpts'])
