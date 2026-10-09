import httpx

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.observability import observe_llm
from stockpilot.telemetry import instrument_httpx


class OllamaAdapter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @observe_llm("generation")
    async def generate(
        self,
        prompt: str,
        response_format: dict[str, object] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "model": self._settings.ollama_model,
            "prompt": prompt,
            "stream": False,
        }
        if response_format is not None:
            payload["format"] = response_format

        async with httpx.AsyncClient(timeout=60.0) as client:
            instrument_httpx(client)
            response = await client.post(
                f"{self._settings.ollama_base_url}/api/generate",
                json=payload,
            )

            response.raise_for_status()

            return response.json()["response"]

    @observe_llm("classification")
    async def classify_intent(self, message: str) -> IntentResult:
        prompt = f"""
You are an intent classifier and information extractor
for an e-commerce assistant.

Classify the user message using exactly one intent:

PRODUCT_ADVICE:
The user asks for advice, recommendations,
or help choosing a tool for drilling, fastening, sawing or sanding.
Implicit needs for these tasks also count as PRODUCT_ADVICE.
Other recommendation domains (music, clothing, food, travel, phones) are UNKNOWN.
This domain restriction applies ONLY to PRODUCT_ADVICE.

STOCK_CHECK:
The user asks whether a product is available,
still available, in stock, or asks about quantity.

ORDER:
The user explicitly wants to buy or order a product.
For ORDER and STOCK_CHECK, do not reject an unknown reference or infer
catalogue membership: the existing business processing handles it.

UNKNOWN:
None of the previous cases.

Extraction rules:

- raw_product:
  Extract the exact product phrase from the user.
  Include the generic category word, brand, and model when present.

  Examples:
  "Est-ce que la perceuse Bosch X100 est encore disponible ?"
  -> "perceuse Bosch X100"

  "une scie Makita"
  -> "scie Makita"

  "cette perceuse"
  -> "perceuse"

- product_name:
  Extract the canonical product name, excluding generic category words
  such as "perceuse", "scie", or "ponceuse". Include brand and model.
  Preserve an explicitly named brand/model even after plural category words
  or quantities, for every intent. raw_product can include the category;
  product_name contains only the concrete reference.
  Keep the entire explicitly named reference, including both brand and model;
  returning the model code alone when its brand is present is incorrect.
  Use null when the user mentions only a generic category without a
  specific product name.
  A category word or a specification (power, voltage, size, price) is not
  a product reference and must not become product_name.
  Decide independently from raw_product: product_name requires a named brand
  or model explicitly in the message. If the message only describes a kind of
  tool, its material/use or technical properties, product_name MUST be JSON null.
  Never return a generic tool noun as product_name, even when raw_product is
  non-null. Do not invent a brand/model to satisfy this rule.

  Examples:
  "perceuse Bosch X100"
  -> "Bosch X100"

  "des scies Acme Z900"
  -> "Acme Z900"

  "une Exemple T300"
  -> "Exemple T300", never just "T300"

  "cette perceuse"
  -> null

- budget:
  Extract only if explicitly mentioned.

- usage:
  Extract only if explicitly mentioned.

- quantity:
  Extract the explicitly requested whole-number quantity faithfully as a signed integer.
  Zero and negative values MUST be preserved. Extraction is NOT validation:
  the business layer will reject invalid quantities. Never remove a minus
  sign, take an absolute value, clamp, or replace an explicit invalid value by 1.
  Product model numbers, prices, voltage and dimensions are NOT quantities.
  Understand both digits and French number words when possible:
  "Commande-moi 3 Bosch X100" -> 3
  "Je veux deux Bosch X100" -> 2
  "Commande-moi une Bosch X100" -> 1
  For an ORDER with no explicit quantity ("Commande-moi la Bosch X100"),
  return 1. For other intents, return null unless a quantity is explicitly
  requested. A question asking how many remain does not provide a quantity:
  use null, never a sentinel such as -1. Do not change, clamp, or invent a
  different quantity.

Do NOT infer category yourself.
The category will be normalized separately by application code.

User message:
{message}
"""

        async with httpx.AsyncClient(timeout=60.0) as client:
            instrument_httpx(client)
            response = await client.post(
                f"{self._settings.ollama_base_url}/api/generate",
                json={
                    "model": self._settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "format": IntentResult.model_json_schema(),
                    "options": {
                        "temperature": 0,
                    },
                },
            )

            response.raise_for_status()

            raw_response = response.json()["response"]
            return IntentResult.model_validate_json(raw_response)
