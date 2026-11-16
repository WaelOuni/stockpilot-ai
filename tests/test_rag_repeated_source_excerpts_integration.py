"""Repeated excerpts through real tenant stores, persisted proofs and history."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from test_tenant_assistant_integration import assistant as assistant  # noqa: F401
from test_tenant_knowledge_integration import command, ingest
from test_tenant_knowledge_integration import environment as environment  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context
from test_tenant_orders_mongo_integration import db as db, orders as orders  # noqa: F401
from test_tenant_stock_operations_mongo_integration import stock as stock  # noqa: F401
from stockpilot.application.services.tenant_assistant_citations import SOURCE_UNAVAILABLE
from stockpilot.application.services.tenant_rag_safety import RAG_CITATION_REFUSAL

pytestmark = pytest.mark.integration
FIRST = "Le musée synthétique ouvre à neuf heures."
SECOND = "La visite synthétique dure trente minutes."
QUESTION = "Résume les documents de mon organisation."


def send(a, question=QUESTION):
    service, conversation, e = a
    return asyncio.run(service.send(REQUESTS["a1"], context(e.orders), conversation,
                                    uuid4().hex, question, read_only=True))


def facts(db):
    return {name: list(db[name].find({}).sort("_id", 1)) for name in
            ("b2b_products", "b2b_orders", "b2b_order_operations", "b2b_stock_movements")}


def proposal(label):
    return json.dumps({"answerable": True, "excerpts": [
        {"source_id": label, "excerpt": FIRST},
        {"source_id": label, "excerpt": SECOND},
    ]})


def test_repeated_excerpts_survive_persistence_followup_and_revoked_history(assistant, db):
    service, conversation, e = assistant
    ingest(e, command("museum-guide", content=FIRST + "\n" + SECOND))
    before = facts(db)
    service.llm.generate.return_value = proposal("S1")
    response = send(assistant)
    expected = f"[S1] « {FIRST} »\n[S1] « {SECOND} »"
    assert response["status"] == "completed" and response["reply"]["text"] == expected
    assert response["reply"]["order"] is None and response["reply"]["can_confirm"] is False
    original = deepcopy(service.store.turn(REQUESTS["a1"], context(e.orders), conversation, response["turn_id"]))
    refs = original["reply"]["cited_sources"]
    assert len(refs) == 1 and refs == original["reply"]["_rag_read_sources"]
    assert refs[0]["organization_id"] == "org-a" and refs[0]["document_id"] == "museum-guide"
    assert service.read(REQUESTS["a1"], context(e.orders), conversation)["items"][0]["reply"]["text"] == expected

    followup = send(assistant, "Cite la source documentaire de ta réponse si elle existe.")
    assert followup["reply"]["intent"] == "RAG_CITATION"
    assert followup["reply"]["cited_sources"] == refs
    assert followup["reply"]["source_turn_id"] == response["turn_id"]
    assert followup["reply"]["text"].count('"museum-guide"') == 1
    service.llm.generate.assert_awaited_once()
    service.llm.classify_intent.assert_not_awaited()

    e.repo.delete(REQUESTS["admin-a"], context(e.orders, "admin-a"), "museum-guide",
                  operation_id=uuid4().hex, expected_revision=1)
    history = service.read(REQUESTS["a1"], context(e.orders), conversation)
    assert all(t["reply"]["text"] == SOURCE_UNAVAILABLE for t in history["items"])
    assert service.store.turn(REQUESTS["a1"], context(e.orders), conversation, response["turn_id"]) == original
    assert facts(db) == before


@pytest.mark.parametrize("revoked", ["museum-guide", "uncited-guide"])
def test_repeated_excerpts_revalidate_cited_and_uncited_real_sources(assistant, db, revoked):
    service, conversation, e = assistant
    ingest(e, command("museum-guide", "museum-operation", FIRST + "\n" + SECOND))
    ingest(e, command("uncited-guide", "other-operation", "Le jardin synthétique ferme le lundi."))
    before = facts(db)
    original_read = e.repo.cited_passage
    e.repo.cited_passage = Mock(wraps=original_read)

    async def generate(*, prompt, response_format=None):
        records = json.loads(prompt.split("<documents>\n")[1].split("\n</documents>")[0])
        label = next(r["source"] for r in records if r["document_id"] == "museum-guide")
        e.repo.delete(REQUESTS["admin-a"], context(e.orders, "admin-a"), revoked,
                      operation_id=uuid4().hex, expected_revision=1)
        return proposal(label)

    service.llm.generate.side_effect = generate
    response = send(assistant)
    assert response["reply"]["text"] == RAG_CITATION_REFUSAL
    stored = service.store.turn(REQUESTS["a1"], context(e.orders), conversation, response["turn_id"])
    assert stored["reply"]["cited_sources"] == stored["reply"]["_rag_read_sources"] == []
    assert e.repo.cited_passage.call_count > 0
    assert facts(db) == before
