"""Documentary scope must not be mistaken for current stock or ORDER authority."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.services.tenant_assistant import TenantAssistant
from stockpilot.application.services.tenant_documentary_routing import documentary_route


REPORTED = (
    "Résume les principaux faits disponibles dans les documents de mon organisation "
    "en citant explicitement les sources de ces faits."
)
DOCUMENTARY = [
    REPORTED,
    "Summarize the available facts in my organization's documents and cite sources.",
    "Explique la procédure documentaire de gestion des stocks faibles.",
    "Résume la procédure d'annulation disponible dans nos documents.",
    "Describe the cancellation procedure in our documents.",
    "Explain the low stock policy in the available documentation.",
    "Que disent les documents sur les ruptures de stock ?",
    "What do the documents say about available stock?",
    "Selon la documentation, comment fonctionne la réservation ?",
    "According to the policy, how are orders cancelled?",
    "Selon le document synthétique, quelle est la durée de conservation ?",
    "According to the policy for orders, how are cancellations handled?",
    "Peux-tu me résumer la procédure de préparation dans les documents ?",
    "Could you please summarize the available company documents?",
]
MIXED = [
    "Résume les documents puis commande 2 SKU DRILL-001.",
    "Résume la documentation et réserve 2 SKU DRILL-001.",
    "Explique la procédure puis confirme cette commande.",
    "Résume les documents et annule la dernière commande.",
    "Summarize the documents and order 2 SKU DRILL-001.",
    "Explain the policy then buy two drills.",
    "Describe the documentation and cancel the order.",
    "Summarize the documents; reserve 2 drills.",
    "Résume les documents, commande 2 SKU DRILL-001.",
    "Summarize the documents and place an order.",
    "Résume les documents puis peux-tu réserver 2 SKU DRILL-001 ?",
    "Summarize the documents and please buy 2 drills.",
    "Summarize the documents: buy 2 drills.",
    "Selon la documentation je veux commander 2 SKU DRILL-001.",
    "Summarize the documents and could you please cancel the order?",
    "Résume les documents.\nPeux-tu réserver 2 SKU DRILL-001 ?",
    "Summarize the documents\nplease buy two drills.",
    "According to the policy buy 2 drills.",
    "Selon la procédure réserve 2 SKU DRILL-001.",
    "According to the policy buy 2 drills, what happens next?",
    "Résume les documents et montre les produits disponibles.",
    "Summarize the policy and show available products.",
    "Selon les documents, quel est le stock actuel de DRILL-001 ?",
    "According to the documents, what is the current stock?",
    "Explique si le SKU DRILL-001 est disponible selon la documentation.",
    "Explain the available quantity of SKU DRILL-001 according to the documents.",
    "Selon les documents, quel est le prix courant ?",
]
OTHER = [
    "Je cherche une perceuse disponible sous 100 euros.",
    "DRILL-001 est-il disponible ?",
    "Quels produits sont bientôt épuisés ?",
    "Show available drills.",
    "Is DRILL-001 in stock?",
    "Montre la fiche du produit Documentation Pro.",
    "What do available drills cost? I have documentation.",
    'Describe the available product "Documentation Pro".',
    "Describe the available product Documentation Pro.",
    "Describe available manual screwdrivers.",
    "Explique les guides de perçage disponibles.",
    "Documents disponibles ?",
    "documentation",
    "disponibles",
    "What is available?",
    "Je veux commander 2 SKU DRILL-001 selon les documents.",
]


@pytest.mark.parametrize("question", DOCUMENTARY)
def test_only_explicit_documentary_read_has_priority(question):
    assert documentary_route(question) == "DOCUMENTARY"


@pytest.mark.parametrize("question", MIXED)
def test_mixed_read_and_business_requests_require_clarification(question):
    assert documentary_route(question) == "DOCUMENTARY_CLARIFICATION"


@pytest.mark.parametrize("question", OTHER)
def test_words_or_model_names_are_not_documentary_intent(question):
    assert documentary_route(question) is None


def service():
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(intent="ORDER", quantity=999, product_name="FOREIGN")))
    orders, catalogue = Mock(), Mock()
    retrieval = SimpleNamespace(execute=AsyncMock(return_value="Documentary fact [S1]."))
    return TenantAssistant(Mock(), catalogue, orders, retrieval, llm)


@pytest.mark.parametrize("question", DOCUMENTARY)
@pytest.mark.parametrize("read_only", [True, False])
@pytest.mark.parametrize("hostile_intent", ["ORDER", "STOCK_CHECK", "PRODUCT_ADVICE"])
def test_document_read_uses_unchanged_retrieval_not_classifier_or_business_stores(question, read_only, hostile_intent):
    s = service()
    s.llm.classify_intent.return_value = IntentResult(intent=hostile_intent, product_name="FOREIGN", quantity=999)
    context = SimpleNamespace(organization_id="org-a", principal=SimpleNamespace(user_id="user-a"))
    state = {"question": question, "turn_id": "same-turn", "read_only": read_only,
             "reply": {"classification": {"intent": hostile_intent}}}
    direct = asyncio.run(s._answer(object(), context, "conversation", state))
    assert direct["intent"] == "RAG" and direct["source_turn_id"] == "same-turn"
    assert not direct["can_confirm"] and direct["order"] is None
    s.retrieval.execute.assert_awaited_once()
    assert s.retrieval.execute.await_args.args[2] == question
    s.orders.assert_not_called()
    assert not s.orders.mock_calls and not s.catalogue.catalogue.mock_calls
    assert state["read_only"] is read_only
    result = asyncio.run(s._graph(object(), context, "conversation", None).ainvoke(state))
    assert result["reply"]["intent"] == "RAG"
    assert result["reply"]["timing_ms"]["classification"] == 0
    s.llm.classify_intent.assert_not_awaited()


@pytest.mark.parametrize("question", MIXED)
@pytest.mark.parametrize("read_only", [True, False])
def test_mixed_intent_is_completed_clarification_without_partial_execution(question, read_only):
    s = service()
    result = asyncio.run(s._graph(object(), object(), "conversation", None).ainvoke({
        "question": question, "read_only": read_only, "turn_id": "same-turn",
    }))
    reply = result["reply"]
    assert reply["intent"] == "CLARIFICATION_REQUIRED"
    assert reply["order"] is None and not reply["can_confirm"]
    assert not s.orders.mock_calls and not s.catalogue.catalogue.mock_calls
    s.retrieval.execute.assert_not_awaited()
    s.llm.classify_intent.assert_not_awaited()


@pytest.mark.parametrize("question,intent", [
    ("Quel est le prix du produit dans les documents ?", "CURRENT_CATALOGUE_PRICE"),
    ("Cite la source documentaire de ta réponse si elle existe", "RAG_CITATION"),
    ("Je cherche une perceuse disponible sous 100 euros.", "PRODUCT_ADVICE"),
    ("Quel est le stock du SKU DRILL-001 ?", "STOCK_CHECK"),
    ("Quels produits sont bientôt épuisés ?", "STOCK_CHECK"),
    ("Je veux commander 2 SKU DRILL-001 selon les documents.", None),
])
def test_existing_exact_and_business_routes_keep_priority(question, intent):
    assert service()._lexical_discovery(question) == intent
