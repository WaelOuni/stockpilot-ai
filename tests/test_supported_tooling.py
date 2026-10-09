import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.domain.services.supported_tooling import has_supported_tooling_signal
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter


@pytest.mark.parametrize("message", [
    "Je cherche une perceuse pour du béton", "Des perceuses",
    "Des visseuses", "Des scies", "Des ponceuses",
    "A drill", "screwdrivers", "saws", "sanders",
    "Quel outil pour couper des panneaux de bois ?",
    "Je cherche quelque chose pour poncer une porte",
    "Que prendre pour visser dans du bois ?",
    "Je veux dévisser une vis", "Je perce du béton",
    "Nous perçons le mur", "Vous poncez une porte",
    "Il scie des panneaux", "Le sciage du bois", "Du ponçage",
])
def test_supported_categories_and_usage_variants(message):
    assert has_supported_tooling_signal(message)


@pytest.mark.parametrize("message", [
    "Quel téléphone me recommandes-tu ?", "Recommande-moi un album de jazz",
    "Quel pantalon me conseilles-tu ?", "Quel restaurant me conseilles-tu ?",
    "Une science intéressante", "Un aperçu du voyage", "Un sawfish",
])
def test_no_supported_signal(message):
    assert not has_supported_tooling_signal(message)
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent="PRODUCT_ADVICE", raw_product="perceuse inventée par le modèle",
    )))
    result = asyncio.run(ClassifyIntentUseCase(llm).execute(message))
    assert result.intent == "UNKNOWN"


@pytest.mark.parametrize("intent", ["ORDER", "STOCK_CHECK", "UNKNOWN"])
def test_domain_guard_does_not_touch_other_intents(intent):
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent=intent, product_name="Acme Z900", quantity=-3,
    )))
    result = asyncio.run(ClassifyIntentUseCase(llm).execute("Acme Z900"))
    assert result.intent == intent
    assert result.product_name == "Acme Z900"
    assert result.quantity == -3


@pytest.mark.parametrize("message", [
    "Quel outil pour couper des panneaux de bois ?",
    "Je cherche quelque chose pour poncer une porte",
    "Que prendre pour visser dans du bois ?",
])
def test_implicit_advice_remains_valid(message):
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=IntentResult(
        intent="PRODUCT_ADVICE",
    )))
    assert asyncio.run(ClassifyIntentUseCase(llm).execute(message)).intent == "PRODUCT_ADVICE"


@pytest.mark.parametrize("phrase,name", [
    ("scies Bosch S500", "Bosch S500"),
    ("3 perceuses Bosch X100", "Bosch X100"),
    ("une Makita D200", "Makita D200"),
    ("scie", None),
])
def test_product_contract_prompt_and_response_passthrough(monkeypatch, phrase, name):
    import json
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"response": json.dumps({
            "intent": "STOCK_CHECK", "raw_product": phrase, "product_name": name,
        })})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    adapter = OllamaAdapter(Settings())
    result = asyncio.run(ClassifyIntentUseCase(adapter).execute(phrase))
    assert result.product_name == name
    assert "Preserve an explicitly named brand/model even after plural category words" in payloads[0]["prompt"]
    assert "must not become product_name" in payloads[0]["prompt"]
