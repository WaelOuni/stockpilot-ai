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
    assert "always extract budget and usage independently" in prompt
    assert '"budget":150' in prompt
    assert '"quantity":-1' in prompt
    assert "if only an operation identifier is provided, order_id MUST be null" in prompt


@pytest.mark.parametrize(("message", "raw_product", "product_name", "category"), [
    ("La perceuse Bosch X100 est-elle encore disponible ?",
     "perceuse Bosch X100", "Bosch X100", "DRILL"),
    ("Est-ce que la scie circulaire Bosch S500 est disponible ?",
     "scie circulaire Bosch S500", "Bosch S500", "SAW"),
    ("Vous reste-t-il des scies Bosch S500 ?",
     "scies Bosch S500", "Bosch S500", "SAW"),
    ("Combien de perceuses Bosch X100 reste-t-il ?",
     "perceuses Bosch X100", "Bosch X100", "DRILL"),
])
def test_stock_prompt_priority_and_extraction(
    monkeypatch, message, raw_product, product_name, category,
):
    import json
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"response": json.dumps({
            "intent": "STOCK_CHECK", "raw_product": raw_product,
            "product_name": product_name, "quantity": None,
        })})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    result = asyncio.run(ClassifyIntentUseCase(OllamaAdapter(Settings())).execute(message))
    assert result.intent.value == "STOCK_CHECK"
    assert result.product_name == product_name
    assert result.category.value == category
    assert result.quantity is None
    prompt = requests[0]["prompt"]
    assert "take priority over\nPRODUCT_ADVICE" in prompt
    assert "For STOCK_CHECK, quantity is null" in prompt
    assert f'Message: "{message}"' in prompt
    assert requests[0]["format"] == IntentResult.model_json_schema()
    assert requests[0]["options"] == {"temperature": 0}


@pytest.mark.parametrize("value", [None, "", " ", "null", " NULL ", "none", " None "])
def test_order_id_null_normalization(value):
    result = IntentResult(intent="ORDER_STATUS", order_id=value)
    assert result.order_id is None
    assert IntentResult(intent="ORDER_STATUS").order_id is None


def test_explicit_order_id_is_preserved_and_delegated_by_classifier():
    order_id = "550e8400-e29b-41d4-a716-446655440000"
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent="ORDER_STATUS", order_id=order_id,
    )))
    result = asyncio.run(ClassifyIntentUseCase(llm).execute("Statut de ma commande"))
    assert result.intent.value == "ORDER_STATUS"
    assert result.order_id == order_id
    llm.classify_intent.assert_awaited_once_with("Statut de ma commande")


@pytest.mark.parametrize(("message", "cleared"), [
    ("Mon identifiant d'opération est {id}.", True),
    ("Quel est le statut de la commande {id} ?", False),
    ("Mon order id est {id}.", False),
    ("operation_id={id}", True),
    ("Mon identifiant d’opération : {id}", True),
    ("Mon identifiant de l'opération est {id}", True),
    ("Operation ID: {id}", True),
    ("operation identifier is {id}", True),
    ("Mon numéro d'opération est {id}", True),
    ("operation_id=550e8400-e29b-41d4-a716-446655440002 ; commande {id}", False),
    ("Mon order id est {id}. Comment obtenir mon operation_id ?", False),
    ("operation_id={id}-autre", False),
])
def test_order_status_operation_identifier_guard(message, cleared):
    identifier = "550e8400-e29b-41d4-a716-446655440001"
    payload = IntentResult(
        intent="ORDER_STATUS", order_id=identifier, raw_product="perceuse Bosch X100",
        product_name="Bosch X100", budget=150, usage="percer", quantity=2,
    )
    original_fields = payload.model_dump(exclude={"order_id", "category"})
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=payload))
    result = asyncio.run(ClassifyIntentUseCase(llm).execute(message.format(id=identifier)))
    assert result.order_id == (None if cleared else identifier)
    assert result.model_dump(exclude={"order_id", "category"}) == original_fields
    assert result.category.value == "DRILL"
    assert result.intent.value == "ORDER_STATUS"


@pytest.mark.parametrize("intent", ["ORDER", "STOCK_CHECK", "PRODUCT_ADVICE", "UNKNOWN"])
def test_operation_identifier_guard_does_not_apply_to_other_intents(intent):
    identifier = "550e8400-e29b-41d4-a716-446655440001"
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent=intent, order_id=identifier,
    )))
    result = asyncio.run(ClassifyIntentUseCase(llm).execute(
        f"perceuse operation_id={identifier}",
    ))
    assert result.order_id == identifier
    assert result.intent.value == intent


@pytest.mark.parametrize("order_id", [123, [], {}])
def test_invalid_order_id_is_rejected(order_id):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        IntentResult(intent="ORDER_STATUS", order_id=order_id)


@pytest.mark.parametrize(("message", "payload"), [
    ("Où en est ma commande ?", {"intent": "ORDER_STATUS", "order_id": None}),
    ("Statut de la commande 550e8400-e29b-41d4-a716-446655440000 ?", {
        "intent": "ORDER_STATUS", "order_id": "550e8400-e29b-41d4-a716-446655440000",
    }),
    ("Où en est ma commande de Bosch X100 ?", {
        "intent": "ORDER_STATUS", "product_name": "Bosch X100", "order_id": "null",
    }),
])
def test_ollama_status_classification_response_and_prompt_schema(monkeypatch, message, payload):
    import json
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"response": json.dumps(payload)})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    result = asyncio.run(OllamaAdapter(Settings()).classify_intent(message))
    assert result.intent.value == "ORDER_STATUS"
    assert result.order_id == (None if payload["order_id"] == "null" else payload["order_id"])
    request = requests[0]
    assert request["format"] == IntentResult.model_json_schema()
    assert "ORDER_STATUS" in request["format"]["$defs"]["Intent"]["enum"]
    schema = request["format"]["properties"]["order_id"]
    assert {"type": "string"} in schema["anyOf"]
    assert {"type": "null"} in schema["anyOf"]
    assert schema["default"] is None
    prompt = request["prompt"]
    assert "ORDER_STATUS:" in prompt
    assert "ORDER:" in prompt
    assert "STOCK_CHECK:" in prompt
    assert "- order_id:" in prompt
    assert "EXISTING order" in prompt
    assert "not ORDER or STOCK_CHECK" in prompt
    assert "identifier explicitly present" in prompt
    assert "Otherwise use null" in prompt
    assert "product/model number as an ID" in prompt
    assert "operation_id with an order_id" in prompt
    assert message in prompt
    assert request["options"] == {"temperature": 0}
    assert request["stream"] is False
