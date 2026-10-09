from stockpilot.application.ports.embedding_port import EmbeddingPort
from stockpilot.application.ports.knowledge_repository import KnowledgeRepositoryPort
from stockpilot.application.ports.llm_port import LlmPort
from stockpilot.application.dto.product_recommendation import ProductRecommendation
import logging
from stockpilot.observability import Timer, event
from stockpilot.telemetry import traced, enrich_span
from stockpilot.metrics import record


logger = logging.getLogger(__name__)


MAX_COSINE_DISTANCE = 0.45
NO_RELEVANT_INFORMATION = (
    "Je n'ai pas trouvé suffisamment d'informations pertinentes pour vous conseiller."
)


class RecommendProductUseCase:
    def __init__(
        self,
        embedding_port: EmbeddingPort,
        knowledge_repository: KnowledgeRepositoryPort,
        llm: LlmPort,
    ):
        self.embedding_port = embedding_port
        self.knowledge_repository = knowledge_repository
        self.llm = llm

    @traced("stockpilot.rag.search")
    async def _search(self, embedding):
        documents = await self.knowledge_repository.search(embedding=embedding, limit=3)
        enrich_span(**{
            "stockpilot.rag.result_count": len(documents),
            "stockpilot.rag.top_distance": min(
                (document.distance for document in documents), default=None
            ),
        })
        return documents

    async def execute(self, question: str) -> ProductRecommendation:
        embedding = await self.embedding_port.embed(question)

        timer = Timer()
        event(logger, "rag_search_started")
        try:
            documents = await self._search(embedding)
        except Exception as error:
            record("rag.searches", outcome="error")
            record("rag.duration", timer.duration_ms / 1000, outcome="error")
            event(logger, "rag_search_failed", level=logging.ERROR,
                  duration_ms=timer.duration_ms, error_type=type(error).__name__)
            raise
        event(logger, "rag_search_completed", duration_ms=timer.duration_ms,
              number_of_results=len(documents),
              top_distance=min((doc.distance for doc in documents), default=None))

        relevant_documents = [
            document
            for document in documents
            if document.distance <= MAX_COSINE_DISTANCE
        ]
        outcome = "results" if relevant_documents else "empty"
        record("rag.searches", outcome=outcome)
        record("rag.duration", timer.duration_ms / 1000, outcome=outcome)

        if not relevant_documents:
            record("rag.empty_results", outcome="empty")
            return ProductRecommendation(
                product_name=None,
                answer=NO_RELEVANT_INFORMATION,
            )

        context = "\n\n---\n\n".join(
            f"Source: {document.source}\n{document.content}"
            for document in relevant_documents
        )

        prompt = f"""
You are a product advisor.

Use ONLY the context below to answer the user's question.
Do not invent product specifications or prices.
If the context is insufficient, say so.
Return JSON matching the requested schema with:
- product_name: when recommending a named product from the context, this MUST be
  its exact canonical product name without a generic category word. For example,
  use "Bosch X100" when recommending the Bosch X100. Use null only if the
  context does not support recommending any specifically named product.
- answer: describe only the product recommendation and supporting details from
  the context, in French. Never mention stock, availability, or say that stock
  information is missing; stock is checked separately by the application.

Context:
{context}

User question:
{question}

Answer in French.
"""

        response = await self.llm.generate(
            prompt,
            response_format=ProductRecommendation.model_json_schema(),
        )
        return ProductRecommendation.model_validate_json(response)