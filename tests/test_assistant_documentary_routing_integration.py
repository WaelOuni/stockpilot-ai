"""Real scoped stores, persisted citations and no ORDER effect for document reads."""

import asyncio
from uuid import uuid4

import pytest

from test_assistant_documentary_routing import REPORTED
from test_tenant_assistant_integration import assistant  # noqa: F401
from test_tenant_knowledge_integration import command, environment, ingest  # noqa: F401
from test_tenant_orders_mongo_integration import REQUESTS, context, db, orders  # noqa: F401
from test_tenant_stock_operations_mongo_integration import stock  # noqa: F401

from stockpilot.application.ports.tenant_knowledge import TenantDataNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("question", [
    REPORTED,
    "Summarize the available facts in my organization's documents and cite sources.",
    "Explique la procédure documentaire de gestion des stocks faibles.",
    "Describe the cancellation procedure in our documents.",
])
@pytest.mark.parametrize("read_only", [True, False])
def test_document_read_is_scoped_cited_persisted_and_never_mutates_order(assistant, db, question, read_only):
    service, conversation, e = assistant
    ingest(e, command("stock-procedure", "ingest-stock", "Le disponible est la quantité physique moins la quantité réservée."))
    ingest(e, command("foreign-guide", "ingest-foreign", "FOREIGN_DOCUMENTARY_DECOY_912"), "admin-b")
    service.llm.generate.return_value = "Le disponible est la quantité physique moins la quantité réservée [S1]."
    watched = ("b2b_products", "b2b_orders", "b2b_order_operations", "b2b_stock_movements")
    before = {name: list(db[name].find({}).sort("_id", 1)) for name in watched}
    turn_id = uuid4().hex
    result = asyncio.run(service.send(REQUESTS["a1"], context(e.orders), conversation,
                                     turn_id, question, read_only=read_only))
    assert result["turn_id"] == turn_id and result["status"] == "completed"
    assert result["reply"]["intent"] == "RAG" and result["operation"] is None
    assert not result["reply"]["can_confirm"] and result["reply"]["order"] is None
    service.llm.classify_intent.assert_not_awaited()
    service.llm.generate.assert_awaited_once()
    prompt = service.llm.generate.await_args.kwargs["prompt"]
    assert "FOREIGN_DOCUMENTARY_DECOY_912" not in prompt
    persisted = service.store.turn(REQUESTS["a1"], context(e.orders), conversation, turn_id)
    assert persisted["input"].get("read_only", False) is read_only and persisted["action"] is None
    assert persisted["reply"]["cited_sources"]
    assert all(s["organization_id"] == "org-a" and s["document_id"] == "stock-procedure"
               for s in persisted["reply"]["cited_sources"])
    assert persisted["reply"]["source_turn_id"] == turn_id
    assert service.read(REQUESTS["a1"], context(e.orders), conversation)["items"][0]["reply"]["cited_sources"]
    with pytest.raises(TenantDataNotFound):
        service.read(REQUESTS["b1"], context(e.orders, "b1"), conversation)
    assert {name: list(db[name].find({}).sort("_id", 1)) for name in watched} == before
    # Persisted authorization, not a lexical documentary cue, controls retrieval.
    old_context = context(e.orders)
    db.b2b_memberships.update_one({"membership_id": "m-a1"}, {"$set": {"status": "suspended"}})
    with pytest.raises(AuthorizationDenied):
        asyncio.run(service.send(REQUESTS["a1"], old_context, conversation,
                                 uuid4().hex, question, read_only=read_only))
    service.llm.generate.assert_awaited_once()
