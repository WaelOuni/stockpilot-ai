import asyncio
from unittest.mock import MagicMock

from stockpilot.application.use_cases.ingest_knowledge import IngestKnowledgeUseCase
from stockpilot.infrastructure.persistence.postgres import (
    pgvector_knowledge_repository,
)


class FakeEmbeddingPort:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.inputs.append(text)
        return [float(len(text))]


class FakeKnowledgeRepositoryPort:
    def __init__(self) -> None:
        self.rows: dict[str | None, tuple[str, list[float]]] = {}
        self.upsert_calls: list[dict[str, object]] = []

    async def upsert(
        self,
        content: str,
        source: str | None,
        embedding: list[float],
    ) -> None:
        self.upsert_calls.append(
            {"content": content, "source": source, "embedding": embedding}
        )
        self.rows[source] = (content, embedding)

    async def search(self, embedding: list[float], limit: int = 3):
        raise NotImplementedError


def test_ingestion_recomputes_embedding_and_upserts_same_source() -> None:
    embedding_port = FakeEmbeddingPort()
    repository = FakeKnowledgeRepositoryPort()
    use_case = IngestKnowledgeUseCase(embedding_port, repository)

    asyncio.run(
        use_case.execute(
            content="Bosch X100: 18V drill",
            source="bosch-x100.txt",
        )
    )
    asyncio.run(
        use_case.execute(
            content="Bosch X100: updated 20V drill",
            source="bosch-x100.txt",
        )
    )

    assert embedding_port.inputs == [
        "Bosch X100: 18V drill",
        "Bosch X100: updated 20V drill",
    ]
    assert len(repository.rows) == 1
    assert repository.rows["bosch-x100.txt"] == (
        "Bosch X100: updated 20V drill",
        [float(len("Bosch X100: updated 20V drill"))],
    )
    assert len(repository.upsert_calls) == 2


def test_postgres_repository_upserts_on_source_conflict(monkeypatch) -> None:
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    monkeypatch.setattr(
        pgvector_knowledge_repository.psycopg,
        "connect",
        MagicMock(return_value=connection_context),
    )
    monkeypatch.setattr(
        pgvector_knowledge_repository,
        "register_vector",
        MagicMock(),
    )
    repository = pgvector_knowledge_repository.PgVectorKnowledgeRepository(
        "postgresql://unused"
    )

    asyncio.run(
        repository.upsert(
            content="Updated product information",
            source="bosch-x100.txt",
            embedding=[0.1, 0.2, 0.3],
        )
    )

    sql = connection.execute.call_args.args[0]
    parameters = connection.execute.call_args.args[1]
    assert "ON CONFLICT (source) DO UPDATE" in sql
    assert "content = EXCLUDED.content" in sql
    assert "embedding = EXCLUDED.embedding" in sql
    assert parameters[0] == "Updated product information"
    assert parameters[1] == "bosch-x100.txt"
