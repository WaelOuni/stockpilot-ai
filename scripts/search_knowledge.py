import asyncio

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
    repository = PgVectorKnowledgeRepository(settings.postgres_url)

    query = "Je cherche une perceuse pour percer du béton avec un budget autour de 150 euros"

    embedding = await embedding_adapter.embed(query)

    results = await repository.search(
        embedding=embedding,
        limit=3,
    )

    print(f"Query: {query}")
    print()

    for index, result in enumerate(results, start=1):
        print(f"--- Result {index} ---")
        print(f"Source: {result.source}")
        print(f"Distance: {result.distance:.4f}")
        print(result.content)
        print()


if __name__ == "__main__":
    asyncio.run(main())