"""Regressions for the attributed-excerpt RAG contract and citation integrity.

The original uncited factual response now produces a neutral abstention.
Historical RED results are retained in the validation reports; these tests
enforce the agreed contract without claiming semantic verification.
Everything is synthetic and in memory, with no persisted or historical turn.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from stockpilot.application.services.tenant_assistant import TenantAssistant
from stockpilot.application.services.tenant_retrieval import TenantRetrieval
from stockpilot.domain.entities.tenant_knowledge import (
    EMBEDDING_DIMENSION,
    TenantPassage,
)
from stockpilot.application.services.tenant_rag_safety import RAG_CITATION_REFUSAL


def exercise(model_answer):
    question = (
        "Résume les principaux faits disponibles dans les documents de mon "
        "organisation en citant explicitement les sources de ces faits."
    )
    passages = [
        TenantPassage(
            organization_id="synthetic-org",
            document_id="synthetic-delivery-guide",
            chunk_id=0,
            content="La livraison prend sept jours ouvrés.",
            distance=0.1,
            generation_id="synthetic-delivery-v1",
        ),
        TenantPassage(
            organization_id="synthetic-org",
            document_id="synthetic-archive-guide",
            chunk_id=0,
            content="Les rapports sont conservés pendant trente jours.",
            distance=0.2,
            generation_id="synthetic-archive-v1",
        ),
    ]
    active = {
        (p.document_id, p.generation_id, p.chunk_id): p for p in passages
    }
    repository = SimpleNamespace(
        current=Mock(),
        search=Mock(return_value=passages),
        cited_passage=Mock(
            side_effect=lambda request, context, doc, generation, chunk:
            active.get((doc, generation, chunk))
        ),
    )
    embeddings = SimpleNamespace(
        embed=AsyncMock(return_value=[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1))
    )
    model = SimpleNamespace(
        generate=AsyncMock(return_value=model_answer),
        classify_intent=AsyncMock(
            side_effect=AssertionError("Explicit documentary routing is required.")
        ),
    )
    retrieval = TenantRetrieval(
        repository, embeddings, model, model="synthetic-embedding", version="v1"
    )
    # Only authorization reads are allowed; no history or persistence API exists.
    store = Mock(spec_set=["current"])
    assistant = TenantAssistant(
        store, Mock(spec_set=[]), Mock(spec_set=[]), retrieval, model
    )
    context = SimpleNamespace(
        organization_id="synthetic-org",
        principal=SimpleNamespace(user_id="synthetic-user"),
    )
    result = asyncio.run(
        assistant._graph(object(), context, "synthetic-conversation", None).ainvoke(
            {"question": question, "turn_id": "synthetic-new-turn", "read_only": True}
        )
    )
    reply = result["reply"]

    embeddings.embed.assert_awaited_once_with(question)
    repository.search.assert_called_once()
    model.generate.assert_awaited_once()
    model.classify_intent.assert_not_awaited()
    prompt = model.generate.await_args.kwargs["prompt"]
    for passage in passages:
        assert passage.content in prompt
        assert passage.document_id in prompt
    # Rejected outputs need neither source revalidation nor a consulted footer.
    # Each test below checks only the postconditions of its own branch.
    return reply, passages, repository


def test_factual_free_form_answer_is_abstained_without_sources():
    """Free prose is no longer returned as a document-supported answer."""
    model_answer = "La livraison prend sept jours ouvrés."
    reply, passages, repository = exercise(model_answer)
    assert reply["intent"] == "RAG"
    assert reply["text"] == "Je n'ai pas trouvé suffisamment d'informations dans les documents disponibles pour répondre de manière fiable."
    assert repository.cited_passage.call_count == 0
    assert reply["cited_sources"] == []
    assert reply["order"] is None and reply["can_confirm"] is False


def test_factual_answer_without_valid_citation_uses_explicit_refusal_fallback():
    """A free form fact cannot bypass exact-excerpt validation."""
    reply, _, _ = exercise("La livraison prend sept jours ouvrés.")
    assert reply["text"].startswith("Je n'ai pas trouvé suffisamment")
    assert "La livraison prend sept jours" not in reply["text"]
    assert reply["cited_sources"] == []


def test_legitimate_abstention_without_citation_is_allowed():
    abstention = json.dumps({"answerable": False, "excerpts": []})
    reply, _, _ = exercise(abstention)
    assert reply["text"].startswith("Je n'ai pas trouvé suffisamment")
    assert reply["cited_sources"] == []


def test_answer_with_valid_citation_is_accepted():
    structured = json.dumps({"answerable": True, "excerpts": [
        {"source_id": "S1", "excerpt": "La livraison prend sept jours ouvrés."},
    ]})
    reply, passages, repository = exercise(
        structured
    )
    assert reply["text"] == "[S1] « La livraison prend sept jours ouvrés. »"
    assert repository.cited_passage.call_count == len(passages)
    assert [s["document_id"] for s in reply["cited_sources"]] == [passages[0].document_id]
    assert [s["document_id"] for s in reply["_rag_read_sources"]] == [
        p.document_id for p in passages
    ]


@pytest.mark.parametrize("label,accepted", [
    ("S99", False),
    ("S1", True),
])
def test_reference_recognition_accepts_authorized_source_and_rejects_unknown(label, accepted):
    answer = json.dumps({"answerable": True, "excerpts": [
        {"source_id": label, "excerpt": "La livraison prend sept jours ouvrés."},
    ]})
    reply, passages, _ = exercise(answer)
    if accepted:
        # S1 actually identifies the authorized delivery document in this fixture.
        assert reply["text"] == "[S1] « La livraison prend sept jours ouvrés. »"
        assert [s["document_id"] for s in reply["cited_sources"]] == [passages[0].document_id]
    else:
        assert reply["text"] == RAG_CITATION_REFUSAL
        assert reply["cited_sources"] == reply["_rag_read_sources"] == []


def test_markers_only_in_server_appendix_do_not_justify_fact():
    """Un unstructured answer cannot mint a server-annex citation."""
    reply, _, _ = exercise("La livraison prend sept jours ouvrés.")
    assert reply["cited_sources"] == []
    assert "[S1]" not in reply["text"]
    assert reply["text"].startswith("Je n'ai pas trouvé suffisamment")


def test_valid_excerpt_renders_source_text_without_free_form_claim():
    """Source attribution displays only the verified excerpt itself."""
    answer = json.dumps({"answerable": True, "excerpts": [
        {"source_id": "S2", "excerpt": "Les rapports sont conservés pendant trente jours."},
    ]})
    reply, passages, _ = exercise(answer)
    assert reply["text"] == "[S2] « Les rapports sont conservés pendant trente jours. »"
    assert [s["document_id"] for s in reply["cited_sources"]] == [passages[1].document_id]
