"""An excerpt is not a distinct passage: validate each, deduplicate proofs only."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from stockpilot.application.ports.tenant_knowledge import TenantDataUnavailable
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.services.tenant_rag_safety import (
    RAG_CITATION_REFUSAL, RAG_INSUFFICIENT_EVIDENCE, RAG_SENSITIVE_REFUSAL,
)
from stockpilot.application.services.tenant_retrieval import TenantRetrieval
from stockpilot.domain.entities.tenant_knowledge import TenantPassage

FIRST = "Le musée synthétique ouvre à neuf heures."
SECOND = "La visite synthétique dure trente minutes."
OTHER = "Le jardin synthétique ferme le lundi."


def draft(*items):
    return json.dumps({"answerable": True, "excerpts": [
        {"source_id": label, "excerpt": excerpt} for label, excerpt in items
    ]}, ensure_ascii=False)


def fixture(raw, passages=None):
    passages = passages if passages is not None else [
        TenantPassage("org-a", "guide", 0, FIRST + "\n" + SECOND, .1, "generation"),
        # A different passage of the SAME document must keep a separate proof.
        TenantPassage("org-a", "guide", 1, OTHER, .2, "generation"),
    ]
    active = {(p.document_id, p.generation_id, p.chunk_id): p for p in passages}
    repo = SimpleNamespace(current=Mock(), search=Mock(return_value=passages),
        cited_passage=Mock(side_effect=lambda r, c, d, g, n: active.get((d, g, n))))
    generator = SimpleNamespace(generate=AsyncMock(return_value=raw))
    service = TenantRetrieval(repo, SimpleNamespace(embed=AsyncMock(return_value=[1.] + [0.] * 767)),
                              generator, model="synthetic", version="v1")
    return SimpleNamespace(service=service, repo=repo, generator=generator,
        context=SimpleNamespace(organization_id="org-a"), passages=passages,
        active=active, cited=[{"stale": True}], consulted=[{"stale": True}])


def execute(h):
    return asyncio.run(h.service.execute(object(), h.context, "Résume les documents.",
                                        citations=h.cited, consulted_sources=h.consulted))


def rendered(*items):
    return "\n".join(f"[{label}] « {excerpt} »" for label, excerpt in items)


def test_two_exact_excerpts_share_one_passage_and_one_proof():
    items = (("S1", FIRST), ("S1", SECOND))
    h = fixture(draft(*items))
    assert execute(h) == rendered(*items)
    assert h.cited == [h.service._reference(h.passages[0])]
    assert h.consulted == [h.service._reference(p) for p in h.passages]
    assert h.repo.cited_passage.call_count == 2


def test_distinct_chunks_and_repeated_labels_preserve_first_citation_order():
    items = (("S2", OTHER), ("S1", FIRST), ("S2", OTHER), ("S1", SECOND))
    h = fixture(draft(*items))
    assert execute(h) == rendered(*items)
    assert h.cited == [h.service._reference(h.passages[i]) for i in (1, 0)]
    assert h.consulted == [h.service._reference(p) for p in h.passages]
    assert len(h.cited) == 2  # Same document, distinct chunks.


def test_different_authorized_documents_keep_distinct_proofs():
    h = fixture(draft(("S1", FIRST), ("S2", OTHER)))
    h.passages[1] = replace(h.passages[1], document_id="other-guide")
    p = h.passages[1]
    h.active[(p.document_id, p.generation_id, p.chunk_id)] = p
    assert execute(h) == rendered(("S1", FIRST), ("S2", OTHER))
    assert [r["document_id"] for r in h.cited] == ["guide", "other-guide"]


@pytest.mark.parametrize("items,expected", [
    ((("S1", FIRST), ("S1", SECOND), ("S99", OTHER)), RAG_CITATION_REFUSAL),
    ((("S1", FIRST), ("S1", "Un fait inventé.")), RAG_INSUFFICIENT_EVIDENCE),
    ((("S1", FIRST), ("S1", FIRST.lower())), RAG_INSUFFICIENT_EVIDENCE),
    ((("S1", FIRST), ("S1", OTHER)), RAG_INSUFFICIENT_EVIDENCE),
    ((("S1", FIRST), ("S1", "x" * 2001)), RAG_INSUFFICIENT_EVIDENCE),
    ((("S1", FIRST), ("S1", "password=FICTITIOUS-REPEATED-SOURCE-TEST")), RAG_SENSITIVE_REFUSAL),
])
def test_repetition_cannot_skip_validation_of_a_later_item(items, expected):
    h = fixture(draft(*items))
    assert execute(h) == expected
    assert h.cited == h.consulted == []


@pytest.mark.parametrize("target,change", [(0, "revoked"), (1, "revoked"),
                                          (0, "content"), (0, "generation")])
def test_repeated_source_still_revalidates_all_consulted_passages(target, change):
    raw = draft(("S1", FIRST), ("S1", SECOND))
    h = fixture(raw)

    async def generate(**kwargs):
        p = h.passages[target]
        key = (p.document_id, p.generation_id, p.chunk_id)
        if change == "revoked":
            h.active.pop(key)
        else:
            h.active[key] = replace(p, **({"content": "Changed."} if change == "content"
                                          else {"generation_id": "replacement"}))
        return raw

    h.generator.generate.side_effect = generate
    assert execute(h) == RAG_CITATION_REFUSAL
    assert h.repo.cited_passage.call_count == target + 1
    assert h.cited == h.consulted == []


def test_repetition_does_not_bypass_permission_revocation():
    raw = draft(("S1", FIRST), ("S1", SECOND))
    h = fixture(raw)

    async def generate(**kwargs):
        h.repo.current.side_effect = AuthorizationDenied("synthetic revocation")
        return raw

    h.generator.generate.side_effect = generate
    with pytest.raises(AuthorizationDenied):
        execute(h)
    assert h.cited == h.consulted == []


def test_exactly_five_excerpts_may_reference_one_passage():
    items = (("S1", FIRST),) * 5
    h = fixture(draft(*items))
    assert execute(h) == rendered(*items)
    assert h.cited == [h.service._reference(h.passages[0])]


def test_six_excerpts_are_rejected_even_when_the_distinct_source_count_is_one():
    h = fixture(draft(*(("S1", FIRST),) * 6))
    assert execute(h) == RAG_INSUFFICIENT_EVIDENCE
    assert h.cited == h.consulted == []


@pytest.mark.parametrize("size,accepted", [(1500, True), (1600, False)])
def test_total_size_limit_counts_repeated_excerpts_not_distinct_sources(size, accepted):
    items = (("S1", "x" * size),) * 5
    raw = draft(*items)
    p = TenantPassage("org-a", "large-guide", 0, "x" * 2000, .1, "generation")
    h = fixture(raw, [p])
    if accepted:
        assert len(raw) <= 8000
        assert execute(h) == rendered(*items)
        assert len(h.cited) == 1 and len(rendered(*items)) <= 8000
    else:
        assert len(raw) > 8000 and len(rendered(*items)) > 8000
        with pytest.raises(TenantDataUnavailable, match="Generation dependency unavailable"):
            execute(h)
        assert h.cited == h.consulted == []


@pytest.mark.parametrize("kind", ["extra_claim", "appended_prose", "unstructured"])
def test_no_free_form_fact_is_published_with_repeated_citations(kind):
    raw = draft(("S1", FIRST), ("S1", SECOND))
    if kind == "extra_claim":
        value = json.loads(raw)
        value["answer"] = "Une conclusion factuelle non vérifiée."
        raw = json.dumps(value)
    elif kind == "appended_prose":
        raw += " Une conclusion factuelle non vérifiée."
    else:
        raw = "Une conclusion factuelle non vérifiée. [S1] [S1]"
    h = fixture(raw)
    assert execute(h) == RAG_INSUFFICIENT_EVIDENCE
    assert h.cited == h.consulted == []


def test_duplicate_historical_proofs_are_still_invalid():
    h = fixture(draft(("S1", FIRST), ("S1", SECOND)))
    reference = h.service._reference(h.passages[0])
    assert h.service.revalidate_citations(object(), h.context, [reference, dict(reference)]) == []


def test_duplicate_retrieval_identities_are_not_silently_deduplicated():
    h = fixture(draft(("S1", FIRST), ("S2", SECOND)))
    h.passages[1] = h.passages[0]
    assert execute(h) == RAG_CITATION_REFUSAL
    assert h.cited == h.consulted == []


def test_foreign_passage_never_reaches_generation_even_with_repeated_labels():
    h = fixture(draft(("S1", FIRST), ("S1", SECOND)))
    h.passages[0] = replace(h.passages[0], organization_id="org-b")
    with pytest.raises(TenantDataUnavailable, match="Invalid scoped retrieval result"):
        execute(h)
    h.generator.generate.assert_not_awaited()
