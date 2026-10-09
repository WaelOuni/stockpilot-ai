import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter


@pytest.mark.parametrize("quantity", [0, -1, -3, 2, 1])
def test_classification_preserves_quantity_and_business_rejects_invalid(quantity):
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent="ORDER", raw_product="perceuse Bosch X100",
        product_name="Bosch X100", quantity=quantity,
    )))
    result = asyncio.run(ClassifyIntentUseCase(llm).execute("order message"))
    assert result.quantity == quantity
    stock = SimpleNamespace(execute=AsyncMock())
    if quantity < 1:
        with pytest.raises(ValueError, match="strictement positif"):
            asyncio.run(PrepareOrderUseCase(stock).execute(result.product_name, result.quantity))
        stock.execute.assert_not_awaited()


@pytest.mark.parametrize("intent", ["ORDER", "STOCK_CHECK"])
def test_unknown_reference_keeps_business_intent(intent):
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent=intent, raw_product="perceuse Acme Z900", product_name="Acme Z900",
    )))
    result = asyncio.run(ClassifyIntentUseCase(llm).execute("unknown reference"))
    assert result.intent == intent
    assert result.product_name == "Acme Z900"


def test_adapter_prompt_contract_and_signed_response(monkeypatch):
    import json
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"response": json.dumps({
            "intent": "ORDER", "product_name": "Bosch X100", "quantity": -3,
        })})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    result = asyncio.run(OllamaAdapter(Settings()).classify_intent("Commande-moi -3 Bosch X100"))
    assert result.quantity == -3
    prompt = requests[0]["prompt"]
    assert "Implicit needs" in prompt
    assert "ONLY to PRODUCT_ADVICE" in prompt
    assert "signed integer" in prompt
    assert "Zero and negative values MUST be preserved" in prompt
    assert "as a positive integer" not in prompt
    assert requests[0]["options"] == {"temperature": 0}
