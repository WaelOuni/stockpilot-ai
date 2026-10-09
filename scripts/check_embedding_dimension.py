import asyncio

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import (
    OllamaEmbeddingAdapter,
)


async def main() -> None:
    settings = Settings()
    adapter = OllamaEmbeddingAdapter(settings)

    embedding = await adapter.embed("Perceuse adaptée au béton")

    print(f"Embedding dimension: {len(embedding)}")
    print(f"First 5 values: {embedding[:5]}")


if __name__ == "__main__":
    asyncio.run(main())