"""New ORDER wording on actual disposable Mongo transactions and PG journals."""
import asyncio
from copy import deepcopy

import pytest
from test_tenant_assistant_integration import (
    action, send, assistant, environment, db, orders, stock,  # noqa: F401
)
from test_tenant_orders_mongo_integration import REQUESTS, context, facts

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.ports.tenant_knowledge import TenantDataConflict
from stockpilot.application.services.identity_authorization import AuthorizationDenied

pytestmark = pytest.mark.integration
SKU = "DEMO-01-0225"
PHRASES = [
    f"Prépare une commande de 2 unités du produit {SKU}",
    f"Prépare une commande de 2 unités de {SKU}",
    f"Prépare-moi une commande de 2 unités du produit {SKU}",
    f"Je voudrais préparer une commande de 2 unités de {SKU}",
]


def product(db):
    db.b2b_products.update_one(
        {"organization_id": "org-a", "sku": "DRILL-001"},
        {"$set": {"sku": SKU}},
    )


@pytest.mark.parametrize("message", PHRASES)
@pytest.mark.parametrize("intent", ["UNKNOWN", "PRODUCT_ADVICE", "ORDER"])
def test_explicit_preparation_replay_confirmation_and_cancel(assistant, db, message, intent):
    product(db)
    service, conv, env = assistant
    service.llm.classify_intent.return_value = IntentResult(intent=intent)
    before = deepcopy(list(db.b2b_products.find({}).sort("_id", 1)))
    turn = send(assistant, message, turn="prepare-natural", customer="customer-org-a")
    assert turn["reply"]["intent"] == "ORDER_PREPARE"
    assert turn["reply"]["order"]["status"] == "prepared"
    assert turn["reply"]["order"]["lines"][0]["quantity"] == 2
    assert list(db.b2b_products.find({}).sort("_id", 1)) == before
    assert send(assistant, message, turn="prepare-natural", customer="customer-org-a") == turn
    with pytest.raises(TenantDataConflict):
        send(assistant, message.replace("2 unités", "3 unités"), turn="prepare-natural", customer="customer-org-a")
    saved = service.store.history(REQUESTS["a1"], context(env.orders), conv)["items"][0]
    assert saved["input"]["message"] == message
    assert saved["action"]["lines"] == [{"sku": SKU, "quantity": 2}]
    confirmed = action(assistant, "confirm", turn, "confirm-natural")
    assert confirmed["reply"]["order"]["status"] == "reserved"
    assert facts(db, sku=SKU)[1] == 2
    assert action(assistant, "confirm", turn, "confirm-natural") == confirmed
    cancelled = action(assistant, "cancel", confirmed, "cancel-natural")
    assert cancelled["reply"]["order"]["status"] == "cancelled"
    assert facts(db, sku=SKU)[1] == 0
    action(assistant, "cancel", confirmed, "cancel-natural")
    assert facts(db, sku=SKU)[1] == 0 and db.b2b_orders.count_documents({}) == 1
    assert facts(db, "org-b")[1] == 0


@pytest.mark.parametrize("problem", ["unknown", "foreign", "inactive", "negative", "fraction", "ambiguous", "insufficient", "missing_buyer", "no_permission"])
def test_preparation_refusals_keep_products_and_orders_untouched(assistant, db, problem):
    product(db)
    service, _, env = assistant
    message = PHRASES[0]
    customer = "customer-org-a"
    if problem == "unknown":
        message = message.replace(SKU, "UNKNOWN-999")
    elif problem == "foreign":
        message = message.replace(SKU, "DRILL-001")
    elif problem == "inactive":
        db.b2b_products.update_one({"organization_id": "org-a", "sku": SKU}, {"$set": {"status": "inactive"}})
    elif problem in {"negative", "fraction", "ambiguous", "insufficient"}:
        value = {"negative": "-2", "fraction": "1.5", "ambiguous": "2 ou 3", "insufficient": "11"}[problem]
        message = message.replace("2 unités", value + " unités")
    elif problem == "missing_buyer":
        customer = None
    else:
        db.b2b_memberships.update_one({"organization_id": "org-a", "user_id": "a1"}, {"$set": {"role": "READ_ONLY"}})
    before = deepcopy(list(db.b2b_products.find({}).sort("_id", 1)))
    if problem == "no_permission":
        with pytest.raises(AuthorizationDenied):
            send(assistant, message, customer=customer)
    else:
        result = send(assistant, message, customer=customer)
        assert result["reply"]["intent"] in {"CLARIFICATION_REQUIRED", "ORDER_REFUSED"}
        assert result["reply"]["order"] is None
    assert list(db.b2b_products.find({}).sort("_id", 1)) == before
    assert db.b2b_orders.count_documents({}) == 0
    assert db.b2b_order_operations.count_documents({}) == 0


def test_original_plan_and_operation_survive_explicit_retry(assistant, db, monkeypatch):
    from stockpilot.application.ports.tenant_orders import TenantOrderOutcomeUnknown
    from stockpilot.application.services.tenant_assistant import AssistantPending

    product(db)
    service, conv, env = assistant
    original = service.orders.prepare
    with monkeypatch.context() as patch:
        def unavailable(*args):
            raise TenantOrderOutcomeUnknown("Synthetic unavailable transaction.")
        patch.setattr(service.orders, "prepare", unavailable)
        with pytest.raises(TenantOrderOutcomeUnknown):
            send(assistant, PHRASES[0], turn="retry-natural", customer="customer-org-a")
    saved = service.store.history(REQUESTS["a1"], context(env.orders), conv)["items"][0]
    key = saved["action"]["operation_id"]
    result = asyncio.run(service.retry(REQUESTS["a1"], context(env.orders), conv, "retry-natural"))
    assert result["reply"]["order"]["status"] == "prepared"
    assert facts(db, sku=SKU)[1] == 0
    saved_after = service.store.history(REQUESTS["a1"], context(env.orders), conv)["items"][0]
    assert saved_after["input"]["message"] == PHRASES[0]
    assert saved_after["action"]["operation_id"] == key
    assert service.orders.prepare == original
