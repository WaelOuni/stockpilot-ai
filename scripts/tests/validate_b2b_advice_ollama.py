"""Manual real-model qualification; invoke explicitly as documented in testing.md."""

import os

import pytest
from test_tenant_assistant_integration import assistant as assistant  # noqa: F401
from test_tenant_assistant_integration import db as db  # noqa: F401
from test_tenant_assistant_integration import environment as environment  # noqa: F401
from test_tenant_assistant_integration import orders as orders  # noqa: F401
from test_tenant_assistant_integration import stock as stock  # noqa: F401
from test_tenant_assistant_integration import send

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def model_url():
    url = os.environ.get("B2B_TEST_OLLAMA_URL")
    if not url:
        pytest.fail("B2B_TEST_OLLAMA_URL required for manual real Ollama validation", pytrace=False)
    return url


def test_real_ollama_generic_advice_is_grounded_and_read_only(assistant, db, model_url):
    service, _, _ = assistant
    service.llm = OllamaAdapter(Settings(ollama_base_url=model_url), max_output_tokens=256)
    classify = service.llm.classify_intent
    classifications = []

    async def observed_classification(question):
        result = await classify(question)
        classifications.append(result)
        return result

    service.llm.classify_intent = observed_classification
    extra = db.b2b_products.find_one({"organization_id": "org-a", "sku": "DRILL-001"})
    extra.pop("_id")
    extra.update(sku="DRILL-002", name="Perceuse Northline compacte", price_minor=4500)
    db.b2b_products.insert_one(extra)
    before = list(db.b2b_products.find({}).sort("_id", 1))
    for question, expected in [
        ("Conseille-moi une perceuse disponible pour mon atelier", ["DRILL-002", "DRILL-001"]),
        ("Je cherche une perceuse disponible à moins de 50 euros", ["DRILL-002", "DRILL-001"]),
        ("Conseille-moi la Perceuse Northline 18V 0476", ["DRILL-001"]),
        ("Conseille-moi une perceuse disponible sous 10 euros pour mon atelier", []),
    ]:
        reply = send(assistant, question)["reply"]
        assert reply["intent"] == "PRODUCT_ADVICE"
        assert [p["sku"] for p in reply["products"]] == expected
        assert all(p["organization_id"] == "org-a" for p in reply["products"])
        assert classifications
        print({
            "question": question,
            "model_classification": classifications[-1].model_dump(),
            "intent": reply["intent"],
            "criteria": reply["criteria"],
            "skus": expected,
        })
    assert list(db.b2b_products.find({}).sort("_id", 1)) == before
    assert db.b2b_orders.count_documents({}) == 0
    assert db.b2b_order_operations.count_documents({}) == 0


def test_real_ollama_persisted_b2b_categories_and_low_stock(assistant, db, model_url):
    from time import perf_counter
    service, _, _ = assistant
    service.llm = OllamaAdapter(Settings(ollama_base_url=model_url), max_output_tokens=256)
    template = db.b2b_products.find_one({"organization_id": "org-a", "sku": "DRILL-001"})
    template.pop("_id")
    db.b2b_products.delete_many({"organization_id": "org-a"})
    for i, price in enumerate((15500, 16000, 16100)):
        db.b2b_products.insert_one({**template, "sku": f"BDEMO-01-{33+i:04}", "category": "perceuses", "name": f"Perceuse Norex {i}", "price_minor": price, "quantity": 20, "reserved": 0})
    before = list(db.b2b_products.find({}).sort("_id", 1))
    for question, expected in [
        ("je cherche 2 perceuses disponibles moins de 150 euros", []),
        ("je cherche 2 perceuses disponibles moins de 300 euros", [15500, 16000]),
        ("je cherche une perceuse disponible moins de 250 euros", [15500, 16000, 16100]),
        ("je cherche une Perceuse disponible moins de 160 euros", [15500]),
        ("je cherche 2 perceuses disponibles maximum 160 euros", [15500, 16000]),
    ]:
        started = perf_counter()
        # Each screenshot prompt is independent; follow-up count persistence is
        # covered separately by the deterministic checkpoint tests.
        from uuid import uuid4
        from test_tenant_orders_mongo_integration import REQUESTS, context
        _, _, environment = assistant
        conversation = service.store.open(REQUESTS["a1"], context(environment.orders), uuid4().hex).conversation_id
        turn = send((service, conversation, environment), question)
        assert turn["status"] == "completed" and turn["operation"] is None
        reply = turn["reply"]
        assert reply["intent"] == "PRODUCT_ADVICE"
        assert [p["price_minor"] for p in reply["products"]] == expected
        assert all(p["organization_id"] == "org-a" for p in reply["products"])
        print({"case": question, "results": len(expected), "elapsed_seconds": round(perf_counter()-started, 2)})
    assert list(db.b2b_products.find({}).sort("_id", 1)) == before
    for i, available in enumerate((0, 4, 5)):
        db.b2b_products.update_one({"organization_id": "org-a", "sku": f"BDEMO-01-{33+i:04}"}, {"$set": {"quantity": available+7, "reserved": 7}})
    before = list(db.b2b_products.find({}).sort("_id", 1))
    for question, count in [
        ("quels sont les produits qui vont s'épuiser, stock moins de 5", 2),
        ("montre-moi les produits en rupture", 1),
        ("quelles perceuses ont 5 ou moins en stock", 3),
    ]:
        # Each screenshot prompt is independent; follow-up count persistence is
        # covered separately by the deterministic checkpoint tests.
        from uuid import uuid4
        from test_tenant_orders_mongo_integration import REQUESTS, context
        _, _, environment = assistant
        conversation = service.store.open(REQUESTS["a1"], context(environment.orders), uuid4().hex).conversation_id
        turn = send((service, conversation, environment), question)
        assert turn["status"] == "completed" and turn["operation"] is None
        assert turn["reply"]["intent"] == "LOW_STOCK" and turn["reply"]["total"] == count
        assert len(turn["reply"]["products"]) == count
    assert list(db.b2b_products.find({}).sort("_id", 1)) == before
    assert db.b2b_orders.count_documents({}) == 0
    assert db.b2b_order_operations.count_documents({}) == 0
