from stockpilot.application.use_cases.catalogue_advice import is_catalogue_advice
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.ports.llm_port import LlmPort
from stockpilot.application.order_identifier import is_explicit_operation_id
from stockpilot.domain.services.category_normalizer import normalize_category
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.services.supported_tooling import has_supported_tooling_signal
from stockpilot.application.order_quantity_evidence import inspect_quantity, explicit_order_request
from stockpilot.application.order_quantity_errors import InvalidOrderQuantityError


class ClassifyIntentUseCase:
    def __init__(self, llm: LlmPort) -> None:
        self._llm = llm

    async def execute(self, message: str) -> IntentResult:
        catalogue_request = is_catalogue_advice(message)
        try:
            result = await self._llm.classify_intent(message)
        except Exception:
            if not catalogue_request:
                raise
            result = IntentResult(intent=Intent.PRODUCT_ADVICE)
        if catalogue_request:
            # Explicit advice language cannot become a mutation through model output.
            result.intent = Intent.PRODUCT_ADVICE
        if result.intent == Intent.ORDER and explicit_order_request(message):
            try:
                evidence = inspect_quantity(message)
            except InvalidOrderQuantityError:
                pass  # Invalid/uncertain user quantities must reach the ORDER refusal.
            else:
                token = ' '.join(message[evidence.start:evidence.end].casefold().split()) if evidence.start is not None else ''
                # This bounded lexical contract already proves 6 or 12. The
                # model's extracted quantity is not authoritative for this span.
                # Do not touch digit counts, defaults or quantitative follow-ups.
                if evidence.kind == 'explicit' and token in {
                    'douzaine', 'une douzaine', 'demi-douzaine',
                    'une demi-douzaine', 'demi douzaine', 'une demi douzaine',
                }:
                    result = result.model_copy(update={'quantity': evidence.quantity})
        result.category = normalize_category(result.raw_product)
        if (
            result.intent == Intent.ORDER_STATUS
            and result.order_id is not None
            and is_explicit_operation_id(message, result.order_id)
        ):
            result.order_id = None
        if result.intent == Intent.PRODUCT_ADVICE and not has_supported_tooling_signal(message) and not catalogue_request:
            result.intent = Intent.UNKNOWN
        return result
