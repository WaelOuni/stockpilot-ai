import httpx  # noqa: F401 - preserve established adapter transport injection boundary

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.resource_limits import post_ollama
from stockpilot.observability import observe_llm


class OllamaAdapter:
    def __init__(
        self, settings: Settings, *, max_output_tokens: int | None = None
    ) -> None:
        self._settings = settings
        if max_output_tokens is not None and (
            type(max_output_tokens) is not int or not 32 <= max_output_tokens <= 1024
        ):
            raise ValueError("Bounded output token limit required.")
        self._max_output_tokens = max_output_tokens

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

        if self._max_output_tokens is not None:
            payload["options"] = {
                "num_predict": self._max_output_tokens,
                "temperature": 0,
            }
        response = await post_ollama(self._settings, "/api/generate", payload, prompt)
        return response["response"]

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
Use this intent for choosing/recommending a tool ("que me conseilles-tu",
"quel outil pour...", "quelle perceuse choisir"), NOT for inventory questions.
Other recommendation domains (music, clothing, food, travel, phones) are UNKNOWN.
This domain restriction applies ONLY to PRODUCT_ADVICE.

STOCK_CHECK:
The user asks whether a product is available,
still available, in stock, or asks about quantity.
Inventory availability and remaining-stock questions take priority over
PRODUCT_ADVICE, even when a tool category or intended use is mentioned.
"reste-t-il", "combien reste-t-il", "encore disponible", "est disponible",
"est-elle disponible" and "en stock" ask about inventory, not tool advice.
Preserve the named product and raw tool category in extraction.

ORDER:
The user explicitly wants to buy or order a product.
This is a request to create a NEW order, not to inspect an existing one.
For ORDER and STOCK_CHECK, do not reject an unknown reference or infer
catalogue membership: the existing business processing handles it.

ORDER_STATUS:
The user asks about the status or progress of an EXISTING order, with an
explicit order identifier or referring to their last/current order.
"Où en est ma commande ?" and "Quel est le statut de ma dernière commande ?"
are ORDER_STATUS, not ORDER or STOCK_CHECK. A status question about an order
is not a request to buy again or a question about product availability.

UNKNOWN:
None of the previous cases.

Extraction rules:

- order_id:
  Extract only an order identifier explicitly present in the user message.
  It must be labelled as an order ID / identifiant de commande or follow
  "commande <id>". An operation_id / identifiant d'opération is NOT an order ID:
  if only an operation identifier is provided, order_id MUST be null.
  Preserve the entire identifier (including UUID hyphens). Otherwise use null:
  the application resolves the last order in the current conversation.
  Never invent an ID, use a product/model number as an ID, or confuse an
  operation_id with an order_id. An order UUID is not a requested quantity.
  For example, "Statut de la commande 47d8fbf9-1234-4567-89ab-123456789abc ?"
  -> order_id "47d8fbf9-1234-4567-89ab-123456789abc", intent ORDER_STATUS.

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

For STOCK_CHECK, quantity is null when asking whether stock exists or how much
remains. "Combien" and "reste-t-il" do not supply a requested order quantity.
Never use -1 or the product model number for an unknown stock count.

Do NOT infer category yourself.
The category will be normalized separately by application code.

Contrastive examples (the same product can have different intents).
Examples do not override extraction of other explicitly mentioned fields:
always extract budget and usage independently of intent, product and order_id.

Message: "La perceuse Bosch X100 est-elle encore disponible ?"
Output: {{"intent":"STOCK_CHECK","raw_product":"perceuse Bosch X100","product_name":"Bosch X100","budget":null,"usage":null,"quantity":null,"order_id":null}}

Message: "Est-ce que la scie circulaire Bosch S500 est disponible ?"
Output: {{"intent":"STOCK_CHECK","raw_product":"scie circulaire Bosch S500","product_name":"Bosch S500","budget":null,"usage":null,"quantity":null,"order_id":null}}

Message: "Vous reste-t-il des scies Bosch S500 ?"
Output: {{"intent":"STOCK_CHECK","raw_product":"scies Bosch S500","product_name":"Bosch S500","budget":null,"usage":null,"quantity":null,"order_id":null}}

Message: "Combien de perceuses Bosch X100 reste-t-il ?"
Output: {{"intent":"STOCK_CHECK","raw_product":"perceuses Bosch X100","product_name":"Bosch X100","budget":null,"usage":null,"quantity":null,"order_id":null}}

Message: "Quelle perceuse choisir pour percer du béton ?"
Output: {{"intent":"PRODUCT_ADVICE","raw_product":"perceuse","product_name":null,"budget":null,"usage":"percer du béton","quantity":null,"order_id":null}}

Message: "Je voudrais une perceuse à percussion avec un budget de 150 euros."
Output: {{"intent":"PRODUCT_ADVICE","raw_product":"perceuse à percussion","product_name":null,"budget":150,"usage":null,"quantity":null,"order_id":null}}

Message: "Commande-moi 2 Bosch X100"
Output: {{"intent":"ORDER","raw_product":"Bosch X100","product_name":"Bosch X100","budget":null,"usage":null,"quantity":2,"order_id":null}}

Message: "Commande-moi -1 Bosch X100"
Output: {{"intent":"ORDER","raw_product":"Bosch X100","product_name":"Bosch X100","budget":null,"usage":null,"quantity":-1,"order_id":null}}

Message: "Où en est ma commande de Bosch X100 ?"
Output: {{"intent":"ORDER_STATUS","raw_product":"Bosch X100","product_name":"Bosch X100","budget":null,"usage":null,"quantity":null,"order_id":null}}

Message: "Quel est le statut de ma commande ? Mon identifiant d'opération est 550e8400-e29b-41d4-a716-446655440001."
Output: {{"intent":"ORDER_STATUS","raw_product":null,"product_name":null,"budget":null,"usage":null,"quantity":null,"order_id":null}}

Message: "Statut de la commande 47d8fbf9-1234-4567-89ab-123456789abc ?"
Output: {{"intent":"ORDER_STATUS","raw_product":null,"product_name":null,"budget":null,"usage":null,"quantity":null,"order_id":"47d8fbf9-1234-4567-89ab-123456789abc"}}

User message:
{message}
"""

        response = await post_ollama(
            self._settings,
            "/api/generate",
            {
                "model": self._settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": IntentResult.model_json_schema(),
                "options": {
                    "temperature": 0,
                    **(
                        {"num_predict": self._max_output_tokens}
                        if self._max_output_tokens is not None
                        else {}
                    ),
                },
            },
            prompt,
        )
        return IntentResult.model_validate_json(response["response"])
