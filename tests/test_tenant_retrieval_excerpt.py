"""Deterministic provenance checks for tenant RAG excerpts."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from stockpilot.application.services.tenant_retrieval import (
    TenantRetrieval,
)
from stockpilot.application.services.tenant_rag_safety import (
    RAG_INSUFFICIENT_EVIDENCE,
    RAG_SENSITIVE_REFUSAL,
)
from stockpilot.application.services.tenant_assistant_citations import TenantAssistantCitations
from stockpilot.application.ports.tenant_knowledge import TenantDataUnavailable
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
from stockpilot.infrastructure.llm import resource_limits
from stockpilot.domain.entities.tenant_knowledge import TenantPassage


def harness():
    passage = TenantPassage(
        "org-a", "delivery-guide", 2,
        "Départ confirmé.\r\nLa livraison prend sept jours ouvrés.",
        0.1, "generation-1",
    )
    service = TenantRetrieval(Mock(), Mock(), Mock(), model="test", version="v1")
    context = SimpleNamespace(organization_id="org-a")
    return service, context, {"S1": passage}, passage


def execute_structured_response(response, passages=None):
    passages = passages or [harness()[3]]
    active = {(p.document_id, p.generation_id, p.chunk_id): p for p in passages}
    repository = SimpleNamespace(
        current=Mock(),
        search=Mock(return_value=passages),
        cited_passage=Mock(side_effect=lambda _request, _context, doc, generation, chunk:
                           active.get((doc, generation, chunk))),
    )
    embeddings = SimpleNamespace(embed=AsyncMock(return_value=[1.0] + [0.0] * 767))
    generator = SimpleNamespace(generate=AsyncMock(return_value=response))
    service = TenantRetrieval(repository, embeddings, generator, model="test", version="v1")
    context = SimpleNamespace(organization_id="org-a")
    citations, consulted = [], []
    result = asyncio.run(service.execute(
        object(), context, "Que dit le document ?",
        citations=citations, consulted_sources=consulted,
    ))
    return result, citations, consulted, generator


def test_excerpt_exactly_present_in_authorized_passage_returns_stable_reference():
    service, context, sources, passage = harness()
    result = service.validate_excerpt(
        context, sources, "S1", "La livraison prend sept jours ouvrés."
    )
    assert result == service._reference(passage)


def test_excerpt_normalizes_nfc_and_line_endings_only():
    service, context, sources, passage = harness()
    decomposed = "De\u0301part confirme\u0301.\nLa livraison prend sept jours ouvrés."
    assert service.validate_excerpt(context, sources, "S1", decomposed) == service._reference(passage)
    assert service.validate_excerpt(context, sources, "S1", "la livraison prend sept jours ouvrés.") is None
    assert service.validate_excerpt(context, sources, "S1", "La livraison  prend sept jours ouvrés.") is None


@pytest.mark.parametrize("source_id,excerpt", [
    ("S99", "La livraison prend sept jours ouvrés."),
    ("S0", "La livraison prend sept jours ouvrés."),
    ("S1", ""),
    ("S1", "   "),
    ("S1", "La livraison prend huit jours ouvrés."),
    ("S1", "x" * 2001),
])
def test_unknown_or_empty_or_absent_or_oversized_excerpt_is_rejected(source_id, excerpt):
    service, context, sources, _ = harness()
    assert service.validate_excerpt(context, sources, source_id, excerpt) is None


def test_passage_from_another_tenant_is_rejected_even_if_source_label_matches():
    service, context, sources, passage = harness()
    sources["S1"] = TenantPassage(
        "org-b", passage.document_id, passage.chunk_id, passage.content,
        passage.distance, passage.generation_id,
    )
    assert service.validate_excerpt(
        context, sources, "S1", "La livraison prend sept jours ouvrés."
    ) is None


def test_source_label_must_resolve_to_an_authorized_retrieval_entry():
    service, context, _, passage = harness()
    # A matching passage outside the per-call allowlist is not authorized evidence.
    assert service.validate_excerpt(context, {}, "S1", passage.content) is None


def test_filtered_sensitive_passage_and_excerpt_are_rejected():
    service, context, sources, passage = harness()
    sources["S1"] = TenantPassage(
        passage.organization_id, passage.document_id, passage.chunk_id,
        "Procédure interne password=FICTITIOUS-EXCERPT-TEST-VALUE", passage.distance,
        passage.generation_id,
    )
    assert service.validate_excerpt(
        context, sources, "S1", "password=FICTITIOUS-EXCERPT-TEST-VALUE"
    ) is None


def test_structured_excerpt_response_renders_only_verified_excerpt_and_separates_sources():
    passage = harness()[3]
    response = json.dumps({"answerable": True, "excerpts": [
        {"source_id": "S1", "excerpt": "La livraison prend sept jours ouvrés."},
    ]})
    rendered, citations, consulted, generator = execute_structured_response(response, [passage])
    assert rendered == "[S1] « La livraison prend sept jours ouvrés. »"
    assert citations == [TenantRetrieval._reference(passage)]
    assert consulted == citations
    generator.generate.assert_awaited_once()
    assert generator.generate.await_args.kwargs["response_format"]["type"] == "object"


@pytest.mark.parametrize("excerpt", [
    "Fait inventé absent du document.",
    "",
    "x" * 2001,
])
def test_structured_missing_or_oversized_excerpt_abstains(excerpt):
    response = json.dumps({"answerable": True, "excerpts": [
        {"source_id": "S1", "excerpt": excerpt},
    ]})
    rendered, citations, consulted, _ = execute_structured_response(response)
    assert rendered == RAG_INSUFFICIENT_EVIDENCE
    assert citations == consulted == []


def test_structured_answer_with_unknown_reference_is_refused():
    response = json.dumps({"answerable": True, "excerpts": [
        {"source_id": "S99", "excerpt": "La livraison prend sept jours ouvrés."},
    ]})
    rendered, citations, consulted, _ = execute_structured_response(response)
    from stockpilot.application.services.tenant_rag_safety import RAG_CITATION_REFUSAL
    assert rendered == RAG_CITATION_REFUSAL
    assert citations == consulted == []


def test_explicit_structured_abstention_is_neutral_and_has_no_citation():
    rendered, citations, consulted, _ = execute_structured_response(
        json.dumps({"answerable": False, "excerpts": []})
    )
    assert rendered == RAG_INSUFFICIENT_EVIDENCE
    assert citations == consulted == []


def test_security_refusal_survives_structured_generation_path():
    rendered, citations, consulted, _ = execute_structured_response(
        "client_secret=FICTITIOUS-EXCERPT-TEST-VALUE"
    )
    assert rendered == RAG_SENSITIVE_REFUSAL
    assert citations == consulted == []


def test_unstructured_fact_and_annex_markers_are_not_returned_as_evidence():
    rendered, citations, consulted, _ = execute_structured_response(
        "La livraison prend sept jours ouvrés.\nDocuments consultés : [S1]"
    )
    assert rendered == RAG_INSUFFICIENT_EVIDENCE
    assert "[S1]" not in rendered
    assert citations == consulted == []


def test_neutral_abstention_survives_public_history_projection():
    service, _, _, _ = harness()
    citations = TenantAssistantCitations(Mock(), service)
    turn = {"reply": {"intent": "RAG", "text": RAG_INSUFFICIENT_EVIDENCE,
                      "cited_sources": [], "_rag_read_sources": []}}
    public = citations.public_reply(object(), SimpleNamespace(), "conversation", turn)
    assert public["text"] == RAG_INSUFFICIENT_EVIDENCE
    assert public["cited_sources"] == []
    assert "_rag_read_sources" not in public


def test_oversized_structured_provider_output_is_rejected_by_existing_bound():
    passages = [harness()[3]]
    response = "{" + '"padding":"' + "x" * 8000 + '"}'
    with pytest.raises(TenantDataUnavailable, match="Generation dependency unavailable"):
        execute_structured_response(response, passages)


def test_existing_ollama_port_forwards_response_schema_without_changing_defaults(monkeypatch):
    requests = []
    original = httpx.AsyncClient

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"response": '{"answerable":false,"excerpts":[]}'} )

    def client(**kwargs):
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(resource_limits.httpx, "AsyncClient", client)
    settings = Settings()
    response_format = {"type": "object", "required": ["answerable"]}
    output = asyncio.run(OllamaAdapter(settings).generate("synthetic", response_format))
    assert output == '{"answerable":false,"excerpts":[]}'
    assert requests[0]["format"] == response_format
    assert "temperature" not in requests[0].get("options", {})
