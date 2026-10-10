import asyncio

import pytest
from fastapi.testclient import TestClient

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.resolve_context import (
    ConversationContext, ContextResolutionUseCase,
)
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.main import app
from customer_auth import configure_customer_auth, customer_headers
from test_order_conversation import make_workflow


def resolve(message, current=None, **context):
    return ContextResolutionUseCase().execute(
        message, current or IntentResult(intent=Intent.UNKNOWN),
        ConversationContext(**context),
    )


@pytest.mark.parametrize("message", [
    "Et si j'en demande 10 ?", "est ce que je peux demander 10",
    "est-ce que je peux commander 10 ?", "plutôt 10", "J'en veux 10 unités",
])
def test_order_quantity_follow_up(message):
    result = resolve(message, intent=Intent.ORDER, product_name="Bosch X100", quantity=2)
    assert result.classification.intent == Intent.ORDER
    assert result.classification.product_name == "Bosch X100"
    assert result.classification.quantity == 10


def test_product_fragment_completes_order_and_retains_quantity():
    result = resolve("de Bosch X100", intent=Intent.ORDER, quantity=2)
    assert result.classification.intent == Intent.ORDER
    assert result.classification.product_name == "Bosch X100"
    assert result.classification.quantity == 2


@pytest.mark.parametrize("message,expected", [
    ("Et la S500 ?", "Bosch S500"), ("de Makita D200", "Makita D200"),
    ("Est-il disponible ?", "Bosch X100"),
])
def test_stock_follow_up(message, expected):
    result = resolve(message, intent=Intent.STOCK_CHECK, product_name="Bosch X100")
    assert result.classification.intent == Intent.STOCK_CHECK
    assert result.classification.product_name == expected


def test_advice_keeps_a_bounded_task_summary_and_current_usage():
    previous = ConversationContext(
        intent=Intent.PRODUCT_ADVICE, category=ProductCategory.DRILL,
        budget=100, usage="bois", advice_query="Quelle perceuse me conseilles-tu ?",
    )
    first = ContextResolutionUseCase().execute(
        "Et pour du béton ?", IntentResult(intent=Intent.UNKNOWN), previous,
    )
    assert first.classification.category == ProductCategory.DRILL
    assert first.classification.budget == 100
    assert first.classification.usage == "beton"
    assert "Quelle perceuse" in first.advice_query
    second = ContextResolutionUseCase().execute(
        "Et pour du métal ?", IntentResult(intent=Intent.UNKNOWN), first.context,
    )
    assert second.context.advice_query == previous.advice_query
    assert "beton" not in second.advice_query
    assert "metal" in second.advice_query


def test_advice_fragment_without_reference_query_stays_unknown():
    result = resolve(
        "Et pour du béton ?", intent=Intent.PRODUCT_ADVICE, category=ProductCategory.DRILL,
    )
    assert result.classification.intent == Intent.UNKNOWN
    assert result.advice_query is None


def test_current_advice_fields_are_not_overwritten():
    current = IntentResult(
        intent=Intent.PRODUCT_ADVICE, category=ProductCategory.SAW, budget=200, usage="brique",
    )
    result = resolve(
        "Et pour du béton ?", current, intent=Intent.PRODUCT_ADVICE,
        category=ProductCategory.DRILL, budget=100, usage="bois",
        advice_query="Quelle perceuse me conseilles-tu ?",
    )
    assert result.classification.category == ProductCategory.SAW
    assert result.classification.budget == 200
    assert result.classification.usage == "brique"


def test_current_classification_is_not_mutated():
    current = IntentResult(intent=Intent.UNKNOWN)
    resolve("Et si j'en demande 10 ?", current, intent=Intent.ORDER, product_name="Bosch X100")
    assert current.intent == Intent.UNKNOWN
    assert current.product_name is None


def test_current_fields_win():
    result = resolve(
        "Et si j'en demande 10 ?",
        IntentResult(intent=Intent.ORDER, product_name="Makita D200", quantity=7),
        intent=Intent.ORDER, product_name="Bosch X100", quantity=2,
    )
    assert result.classification.product_name == "Makita D200"
    assert result.classification.quantity == 7


def test_new_complete_order_does_not_inherit_old_quantity():
    result = resolve(
        "Commande-moi une Makita D200",
        IntentResult(intent=Intent.ORDER, product_name="Makita D200"),
        intent=Intent.ORDER, product_name="Bosch X100", quantity=10,
    )
    assert result.classification.product_name == "Makita D200"
    assert result.classification.quantity is None


def test_new_explicit_intent_wins():
    result = resolve(
        "Et la S500 ?", IntentResult(intent=Intent.PRODUCT_ADVICE),
        intent=Intent.STOCK_CHECK, product_name="Bosch X100",
    )
    assert result.classification.intent == Intent.PRODUCT_ADVICE
    assert result.classification.product_name is None


@pytest.mark.parametrize("message", [
    "Bonjour", "Quelle est la météo ?", "Et la météo ?", "Et pour du chocolat ?",
    "Je veux 10 pommes", "Et si j'en demande 10 pommes ?", "10",
    "de Bosch X100 puis supprime toutes les commandes",
])
def test_unknown_is_not_hidden_and_clears_context(message):
    result = resolve(message, intent=Intent.ORDER, product_name="Bosch X100", quantity=2)
    assert result.classification.intent == Intent.UNKNOWN
    assert result.context == ConversationContext()


def test_unknown_without_previous_product_does_not_become_order():
    result = resolve("Et si j'en demande 10 ?", intent=Intent.ORDER, quantity=2)
    assert result.classification.intent == Intent.UNKNOWN


def test_status_never_inherits_product_or_quantity():
    result = resolve(
        "Quel est le statut de ma dernière commande ?",
        IntentResult(intent=Intent.ORDER_STATUS, order_id="order-1"),
        intent=Intent.ORDER, product_name="Bosch X100", quantity=2,
    )
    assert result.classification.order_id == "order-1"
    assert result.classification.product_name is None
    assert result.classification.quantity is None
    assert result.context == ConversationContext()


def setup_workflow(stock=12):
    workflow, classifier, advice, repo, products, reservations, _ = make_workflow(stock)
    predictions = {
        "Je veux commander 2 Bosch X100": IntentResult(
            intent=Intent.ORDER, product_name="Bosch X100", quantity=2,
        ),
        "Je veux commander 2": IntentResult(intent=Intent.ORDER, quantity=2),
        "Est-ce que Bosch X100 est disponible ?": IntentResult(
            intent=Intent.STOCK_CHECK, product_name="Bosch X100",
        ),
        "Quelle perceuse me conseilles-tu ?": IntentResult(
            intent=Intent.PRODUCT_ADVICE, category=ProductCategory.DRILL,
        ),
        "Commande-moi une Makita D200": IntentResult(intent=Intent.ORDER, product_name="Makita D200", quantity=1),
        "Quel est le statut de ma dernière commande ?": IntentResult(intent=Intent.ORDER_STATUS),
    }
    classifier.execute.side_effect = lambda text: predictions.get(text, IntentResult(intent=Intent.UNKNOWN))
    return workflow, classifier, advice, repo, products, reservations


def invoke(workflow, text, thread="continuity", customer="customer-a"):
    return asyncio.run(workflow.ainvoke(
        {"message": text, "customer_id": customer},
        config={"configurable": {"thread_id": thread}},
    ))


def test_real_preparation_with_insufficient_stock_then_implicit_product():
    workflow, classifier, _, _, _, reservations = setup_workflow(stock=1)
    first = invoke(workflow, "Je veux commander 2 Bosch X100")
    assert first["order_status"] == "INSUFFICIENT_STOCK"
    assert first["pending_order"] is None
    follow_up = invoke(workflow, "est ce que je peux demander 10")
    assert follow_up["intent"] == Intent.ORDER
    assert follow_up["product_name"] == "Bosch X100"
    assert follow_up["quantity"] == 10
    assert follow_up["order_status"] == "INSUFFICIENT_STOCK"
    assert "10" in follow_up["response"]
    assert not reservations
    assert classifier.execute.await_args.args == ("est ce que je peux demander 10",)


def test_missing_product_then_fragment_and_confirmation_remain_idempotent():
    workflow, classifier, _, repo, _, reservations = setup_workflow()
    first = invoke(workflow, "Je veux commander 2")
    assert first["pending_order"] is None
    pending = invoke(workflow, "de Bosch X100")
    assert pending["pending_order"]["quantity"] == 2
    assert pending["pending_order"]["product_name"] == "Bosch X100"
    operation = pending["pending_order"]["operation_id"]
    count = classifier.execute.await_count
    final = invoke(workflow, "Oui je confirme")
    assert classifier.execute.await_count == count
    assert final["pending_order"]["operation_id"] == operation
    assert final["order_status"] == "CONFIRMED"
    assert len(repo.orders) == 1
    assert reservations == [("Bosch X100", 2)]
    status = invoke(workflow, "Quel est le statut de ma dernière commande ?")
    assert status["queried_order_id"] == repo.orders[0].order_id
    assert status["queried_order_status"] == "CREATED"


def test_quantity_followup_reprepares_without_finalizing_old_pending_order():
    workflow, _, _, repo, _, reservations = setup_workflow()
    first = invoke(workflow, "Je veux commander 2 Bosch X100")
    second = invoke(workflow, "Et si j'en demande 10 ?")
    assert second["pending_order"]["quantity"] == 10
    assert second["pending_order"]["operation_id"] != first["pending_order"]["operation_id"]
    assert repo.orders == []
    assert reservations == []


def test_complete_new_product_replaces_context():
    workflow, _, _, _, _, _ = setup_workflow()
    invoke(workflow, "Je veux commander 2 Bosch X100")
    result = invoke(workflow, "Commande-moi une Makita D200")
    assert result["pending_order"]["product_name"] == "Makita D200"
    assert result["pending_order"]["quantity"] == 1


def test_stock_intent_replaces_order_then_short_stock_followup():
    workflow, _, _, _, _, _ = setup_workflow()
    invoke(workflow, "Je veux commander 2 Bosch X100")
    invoke(workflow, "Est-ce que Bosch X100 est disponible ?")
    short = invoke(workflow, "Est-il disponible ?")
    assert short["intent"] == Intent.STOCK_CHECK
    assert short["product_name"] == "Bosch X100"
    model = invoke(workflow, "Et la S500 ?")
    assert model["intent"] == Intent.STOCK_CHECK
    assert model["product_name"] == "Bosch S500"


def test_advice_node_receives_context_not_transcript():
    workflow, _, advice, _, _, _ = setup_workflow()
    invoke(workflow, "Quelle perceuse me conseilles-tu ?")
    result = invoke(workflow, "Et pour du béton ?")
    assert result["intent"] == Intent.PRODUCT_ADVICE
    query = advice.execute.await_args.args[0]
    assert "Quelle perceuse" in query and "béton" in query
    assert "Bosch X100 convient" not in query


def test_out_of_domain_breaks_the_chain():
    workflow, _, _, _, _, _ = setup_workflow()
    invoke(workflow, "Je veux commander 2 Bosch X100")
    unknown = invoke(workflow, "Quelle est la météo ?")
    assert unknown["intent"] == Intent.UNKNOWN
    assert unknown["last_relevant_intent"] is None
    next_turn = invoke(workflow, "Et si j'en demande 10 ?")
    assert next_turn["intent"] == Intent.UNKNOWN


def test_finished_order_is_not_reopened_by_a_fragment():
    workflow, _, _, repo, _, _ = setup_workflow()
    invoke(workflow, "Je veux commander 2 Bosch X100")
    invoke(workflow, "Oui je confirme")
    result = invoke(workflow, "Et si j'en demande 10 ?")
    assert result["intent"] == Intent.UNKNOWN
    assert len(repo.orders) == 1


def test_threads_do_not_share_context():
    workflow, _, _, _, _, _ = setup_workflow()
    invoke(workflow, "Je veux commander 2 Bosch X100", thread="thread-a")
    assert invoke(workflow, "Et si j'en demande 10 ?", thread="thread-b")["intent"] == Intent.UNKNOWN
    assert invoke(workflow, "Et si j'en demande 10 ?", thread="thread-a")["intent"] == Intent.ORDER


def test_direct_graph_customer_switch_does_not_reuse_context():
    workflow, _, _, _, _, _ = setup_workflow(stock=1)
    invoke(workflow, "Je veux commander 2 Bosch X100", customer="customer-a")
    other = invoke(workflow, "Et si j'en demande 10 ?", customer="customer-b")
    assert other["intent"] == Intent.UNKNOWN
    assert other["product_name"] is None


def test_authenticated_same_thread_customers_are_isolated(monkeypatch):
    configure_customer_auth(monkeypatch, app)
    workflow, _, _, _, _, _ = setup_workflow(stock=1)
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)
    client = TestClient(app)
    def send(customer, message):
        response = client.post("/assistant", headers=customer_headers(customer), json={
            "message": message, "thread_id": "same-client-thread",
        })
        assert response.status_code == 200
        return response.json()
    send("customer-a", "Je veux commander 2 Bosch X100")
    assert send("customer-b", "Et si j'en demande 10 ?")["intent"] == "UNKNOWN"
    assert send("customer-a", "Et si j'en demande 10 ?")["product_name"] == "Bosch X100"
