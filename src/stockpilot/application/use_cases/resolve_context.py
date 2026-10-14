import re
import unicodedata
from dataclasses import dataclass

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory


@dataclass(frozen=True)
class ConversationContext:
    intent: Intent | None = None
    product_name: str | None = None
    quantity: int | float | bool | None = None
    category: ProductCategory | None = None
    budget: float | None = None
    usage: str | None = None
    advice_query: str | None = None


@dataclass(frozen=True)
class ContextResolution:
    classification: IntentResult
    context: ConversationContext
    advice_query: str | None = None
    product_from_context: bool = False


def _normalize(message: str) -> str:
    value = "".join(
        char for char in unicodedata.normalize("NFKD", message.casefold())
        if not unicodedata.combining(char)
    ).replace("’", "'")
    return re.sub(r"\s+", " ", value).strip().rstrip("?.!").strip()


_QUANTITY = re.compile(
    r"(?:et si j'en (?:demande|commande)|"
    r"(?:est ce que|est-ce que) je peux (?:demander|commander)|"
    r"(?:et )?(?:je voudrais|je veux|j'en veux|plutot|finalement)) (\d+)"
    r"(?: (?:unites?|exemplaires?))?"
)
_PRODUCT = re.compile(
    r"(?:et (?:la|le)|(?:de|du)|(?:plutot|finalement)) "
    r"(?P<product>(?:[A-Za-z][A-Za-z-]* )?[A-Za-z]+\d+[A-Za-z0-9-]*)",
    re.IGNORECASE,
)
_STOCK = re.compile(
    r"(?:et )?(?:est[- ](?:il|elle) disponible|"
    r"(?:est ce que|est-ce que) (?:il|elle) est disponible|"
    r"il y en a en stock|(?:et )?le stock)"
)
_ADVICE_USAGE = re.compile(
    r"(?:et )?pour (?:du |de la |le |la |des )?"
    r"(beton|bois|metal|acier|brique|maconnerie|placo)"
)
_ADVICE_BUDGET = re.compile(r"(?:et )?(?:avec un budget de|pour|budget) (\d+) (?:euros?|eur)")
_ADVICE_REPEAT = re.compile(
    r"(?:propose[- ]moi|recommande[- ]moi) (?:un|deux|trois|quatre|cinq|[1-5]) "
    r"produits? dans mon budget"
)
_RELEVANT = {Intent.ORDER, Intent.STOCK_CHECK, Intent.PRODUCT_ADVICE}


class ContextResolutionUseCase:
    def execute(
        self, message: str, current: IntentResult, previous: ConversationContext,
    ) -> ContextResolution:
        result = current.model_copy()
        normalized = _normalize(message)
        quantity = _QUANTITY.fullmatch(normalized)
        product = _PRODUCT.fullmatch(message.strip().rstrip("?.!").strip())
        stock = _STOCK.fullmatch(normalized)
        usage = _ADVICE_USAGE.fullmatch(normalized)
        budget = _ADVICE_BUDGET.fullmatch(normalized)
        repeat_advice = _ADVICE_REPEAT.fullmatch(normalized)

        continuation = bool(
            (previous.intent == Intent.ORDER and (quantity or product))
            or (previous.intent == Intent.STOCK_CHECK and (stock or product))
            or (previous.intent == Intent.PRODUCT_ADVICE and previous.advice_query and (usage or budget or repeat_advice))
        )
        compatible = continuation and result.intent in (Intent.UNKNOWN, previous.intent)
        if compatible and previous.intent is not None:
            if result.intent == Intent.UNKNOWN:
                result.intent = previous.intent
            if product and result.product_name is None:
                fragment = product.group("product")
                if " " in fragment:
                    result.product_name = fragment
                elif previous.product_name:
                    # Only expand a model shorthand if the previous brand/model is clear.
                    parts = previous.product_name.split()
                    if len(parts) == 2 and re.fullmatch(r"[A-Za-z]+\d+[A-Za-z0-9-]*", parts[1]):
                        result.product_name = f"{parts[0]} {fragment}"
            elif result.product_name is None:
                result.product_name = previous.product_name
            if result.intent == Intent.ORDER:
                if result.quantity is None:
                    result.quantity = int(quantity.group(1)) if quantity else previous.quantity
                if result.product_name is None:
                    # A quantity alone is not enough to recover an unknown order.
                    if current.intent == Intent.UNKNOWN:
                        result = current.model_copy()
            if result.intent == Intent.STOCK_CHECK and result.product_name is None and current.intent == Intent.UNKNOWN:
                result = current.model_copy()

        advice_query = None
        if compatible and result.intent == Intent.PRODUCT_ADVICE and previous.advice_query:
            result.category = result.category if result.category is not None else previous.category
            result.budget = result.budget if result.budget is not None else (
                float(budget.group(1)) if budget else previous.budget
            )
            result.usage = result.usage if result.usage is not None else (
                usage.group(1) if usage else previous.usage
            )
            # A bounded task summary, never the raw transcript or a growing chain of prompts.
            details = []
            if result.category is not None:
                details.append(f"Categorie : {result.category.value}.")
            if result.budget is not None:
                details.append(f"Budget : {result.budget:g} euros.")
            if result.usage:
                details.append(f"Usage : {result.usage}.")
            advice_query = f"{previous.advice_query}\n{' '.join(details)}\nPrecision : {message}"

        if result.intent not in _RELEVANT:
            return ContextResolution(result, ConversationContext())
        context = ConversationContext(
            intent=result.intent, product_name=result.product_name, quantity=result.quantity,
            category=result.category, budget=result.budget, usage=result.usage,
            advice_query=previous.advice_query if advice_query else (
                message if result.intent == Intent.PRODUCT_ADVICE else None
            ),
        )
        return ContextResolution(result, context, advice_query, bool(
            compatible and not product and result.product_name == previous.product_name
            and result.product_name is not None
        ))
