from stockpilot.application.ports.embedding_port import EmbeddingPort
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.resource_limits import post_ollama


class OllamaEmbeddingAdapter(EmbeddingPort):
    def __init__(self, settings: Settings):
        self._settings = settings
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_embedding_model

    async def embed(self, text: str) -> list[float]:
        payload = await post_ollama(
            self._settings, "/api/embed",
            {
                "model": self.model,
                "input": text,
            }, text,
        )
        return payload["embeddings"][0]
