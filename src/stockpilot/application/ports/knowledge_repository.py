from typing import Protocol

from stockpilot.application.dto.knowledge_result import KnowledgeResult


class KnowledgeRepositoryPort(Protocol):
    async def upsert(
        self,
        content: str,
        source: str | None,
        embedding: list[float],
    ) -> None:
        ...

    async def search(
        self,
        embedding: list[float],
        limit: int = 3,
    ) -> list[KnowledgeResult]:
        ...