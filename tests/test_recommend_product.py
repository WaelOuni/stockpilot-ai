import asyncio
from unittest.mock import AsyncMock

from stockpilot.application.dto.knowledge_result import KnowledgeResult
from stockpilot.application.dto.product_recommendation import ProductRecommendation
from stockpilot.application.use_cases.recommend_product import (
    NO_RELEVANT_INFORMATION,
    RecommendProductUseCase,
)


class FakeEmbeddingPort:
    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class FakeKnowledgeRepositoryPort:
    def __init__(self, documents: list[KnowledgeResult]) -> None:
        self.documents = documents
        self.search = AsyncMock(return_value=documents)


def test_relevant_documents_are_sent_to_llm_with_sources_not_distances() -> None:
    documents = [
        KnowledgeResult(
            content="Bosch X100 has a brushless motor.",
            source="catalogue",
            distance=0.2,
        ),
        KnowledgeResult(
            content="Bosch X100 includes two batteries.",
            source="manual",
            distance=0.4,
        ),
        KnowledgeResult(
            content="Irrelevant information.",
            source="other",
            distance=0.46,
        ),
    ]
    repository = FakeKnowledgeRepositoryPort(documents)
    llm = AsyncMock()
    llm.generate = AsyncMock(
        return_value=(
            '{"product_name":"Bosch X100",'
            '"answer":"Le Bosch X100 possède un moteur sans balais."}'
        )
    )
    use_case = RecommendProductUseCase(FakeEmbeddingPort(), repository, llm)

    response = asyncio.run(use_case.execute("Décris le Bosch X100"))

    assert response == ProductRecommendation(
        product_name="Bosch X100",
        answer="Le Bosch X100 possède un moteur sans balais.",
    )
    repository.search.assert_awaited_once_with(embedding=[0.1, 0.2, 0.3], limit=3)
    llm.generate.assert_awaited_once()
    prompt = llm.generate.await_args.args[0]
    assert "Bosch X100" in prompt
    assert "Never mention stock, availability" in prompt
    assert llm.generate.await_args.kwargs["response_format"] == (
        ProductRecommendation.model_json_schema()
    )
    assert "Source: catalogue\nBosch X100 has a brushless motor." in prompt
    assert "Source: manual\nBosch X100 includes two batteries." in prompt
    assert "Irrelevant information." not in prompt
    assert "0.2" not in prompt
    assert "0.4" not in prompt


def test_documents_above_threshold_return_fallback_without_calling_llm() -> None:
    repository = FakeKnowledgeRepositoryPort(
        [
            KnowledgeResult(content="Low relevance.", source="source", distance=0.46),
            KnowledgeResult(content="Also low relevance.", source="source", distance=0.8),
        ]
    )
    llm = AsyncMock()
    use_case = RecommendProductUseCase(FakeEmbeddingPort(), repository, llm)

    response = asyncio.run(use_case.execute("Question without relevant results"))

    assert response == ProductRecommendation(
        product_name=None,
        answer=NO_RELEVANT_INFORMATION,
    )
    llm.generate.assert_not_awaited()


def test_empty_search_returns_fallback_without_calling_llm() -> None:
    repository = FakeKnowledgeRepositoryPort([])
    llm = AsyncMock()
    use_case = RecommendProductUseCase(FakeEmbeddingPort(), repository, llm)

    response = asyncio.run(use_case.execute("Question without search results"))

    assert response == ProductRecommendation(
        product_name=None,
        answer=NO_RELEVANT_INFORMATION,
    )
    llm.generate.assert_not_awaited()


def test_document_at_threshold_is_relevant() -> None:
    repository = FakeKnowledgeRepositoryPort(
        [
            KnowledgeResult(
                content="Threshold document.",
                source="catalogue",
                distance=0.45,
            )
        ]
    )
    llm = AsyncMock()
    llm.generate = AsyncMock(
        return_value='{"product_name":"Produit X","answer":"Réponse générée."}'
    )
    use_case = RecommendProductUseCase(FakeEmbeddingPort(), repository, llm)

    response = asyncio.run(use_case.execute("Question about the product"))

    assert response == ProductRecommendation(
        product_name="Produit X",
        answer="Réponse générée.",
    )
    llm.generate.assert_awaited_once()
    assert "Source: catalogue\nThreshold document." in llm.generate.await_args.args[0]
