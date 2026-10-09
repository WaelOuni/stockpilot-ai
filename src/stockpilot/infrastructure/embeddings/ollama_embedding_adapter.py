import httpx

from stockpilot.application.ports.embedding_port import EmbeddingPort
from stockpilot.infrastructure.config.settings import Settings


class OllamaEmbeddingAdapter(EmbeddingPort):
    def __init__(self, settings: Settings):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_embedding_model

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": text,
                },
            )

            response.raise_for_status()

            payload = response.json()

            return payload["embeddings"][0]