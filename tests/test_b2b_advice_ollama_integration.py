"""Opt-in real classifier, using only canonical disposable integration fixtures."""

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

pytestmark = [pytest.mark.integration, pytest.mark.integration_optional]


def test_real_ollama_generic_advice_is_grounded_and_read_only(assistant, db):
    url = os.environ.get("B2B_TEST_OLLAMA_URL")
    if not url:
        pytest.skip("B2B_TEST_OLLAMA_URL required for opt-in real Ollama validation")
    service, _, _ = assistant
    service.llm = OllamaAdapter(Settings(ollama_base_url=url), max_output_tokens=256)
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
