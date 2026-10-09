from typing import Protocol

from stockpilot.application.dto.intent_result import IntentResult


class LlmPort(Protocol):
    async def generate(
        self,
        prompt: str,
        response_format: dict[str, object] | None = None,
    ) -> str: ...

    async def classify_intent(self, message: str) -> IntentResult: ...
