from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.ports.llm_port import LlmPort
from stockpilot.application.order_identifier import is_explicit_operation_id
from stockpilot.domain.services.category_normalizer import normalize_category
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.services.supported_tooling import has_supported_tooling_signal


class ClassifyIntentUseCase:
    def __init__(self, llm: LlmPort) -> None:
        self._llm = llm

    async def execute(self, message: str) -> IntentResult:
        result = await self._llm.classify_intent(message)
        result.category = normalize_category(result.raw_product)
        if (
            result.intent == Intent.ORDER_STATUS
            and result.order_id is not None
            and is_explicit_operation_id(message, result.order_id)
        ):
            result.order_id = None
        if result.intent == Intent.PRODUCT_ADVICE and not has_supported_tooling_signal(message):
            result.intent = Intent.UNKNOWN
        return result
