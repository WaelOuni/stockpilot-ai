import asyncio
import json

import httpx
import pytest

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter


@pytest.mark.parametrize("message,product", [
    ("une perceuse de 850 W", None),
    ("une perceuse 18V", None),
    ("une scie 1200 W", None),
    ("une ponceuse 125 mm", None),
    ("une visseuse pour bois", None),
    ("Bosch X100", "Bosch X100"),
    ("scies Bosch S500", "Bosch S500"),
    ("Makita D200", "Makita D200"),
])
def test_prompt_distinguishes_generic_specs_from_named_references(monkeypatch, message, product):
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"response": json.dumps({
            "intent": "PRODUCT_ADVICE", "raw_product": message, "product_name": product,
        })})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    result = asyncio.run(OllamaAdapter(Settings()).classify_intent(message))
    assert result.product_name == product
    assert "product_name MUST be JSON null" in captured[0]["prompt"]
    assert "including both brand and model" in captured[0]["prompt"]
    assert captured[0]["options"]["temperature"] == 0
