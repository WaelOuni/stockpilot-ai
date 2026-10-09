import asyncio
from pathlib import Path

from stockpilot.application.use_cases.ingest_knowledge import IngestKnowledgeUseCase
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import (
    OllamaEmbeddingAdapter,
)
from stockpilot.infrastructure.persistence.postgres.pgvector_knowledge_repository import (
    PgVectorKnowledgeRepository,
)


async def main() -> None:
    settings = Settings()

    embedding_adapter = OllamaEmbeddingAdapter(settings)

    repository = PgVectorKnowledgeRepository(
        settings.postgres_url
    )

    use_case = IngestKnowledgeUseCase(
        embedding_adapter,
        repository,
    )

    products_dir = Path("data/products")

    for file in products_dir.glob("*.txt"):
        content = file.read_text(encoding="utf-8")

        await use_case.execute(
            content=content,
            source=file.name,
        )

        print(f"Ingested: {file.name}")


if __name__ == "__main__":
    asyncio.run(main())