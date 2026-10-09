from stockpilot.application.ports.llm_port import LlmPort


class GenerateResponseUseCase:
    def __init__(self, llm: LlmPort) -> None:
        self._llm = llm

    async def execute(self, prompt: str) -> str:
        return await self._llm.generate(prompt)
