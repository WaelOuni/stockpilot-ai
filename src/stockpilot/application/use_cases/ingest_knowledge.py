from stockpilot.application.ports.embedding_port import EmbeddingPort
from stockpilot.application.ports.knowledge_repository import KnowledgeRepositoryPort


class IngestKnowledgeUseCase:
    def __init__(
        self,
        embedding_port: EmbeddingPort,
        knowledge_repository: KnowledgeRepositoryPort,
    ):
        self.embedding_port = embedding_port
        self.knowledge_repository = knowledge_repository

    async def execute(
        self,
        content: str,
        source: str | None = None,
    ) -> None:
        embedding = await self.embedding_port.embed(content)

        await self.knowledge_repository.upsert(
            content=content,
            source=source,
            embedding=embedding,
        )