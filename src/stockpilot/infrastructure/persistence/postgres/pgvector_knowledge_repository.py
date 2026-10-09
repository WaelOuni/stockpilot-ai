import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from stockpilot.application.dto.knowledge_result import KnowledgeResult
from stockpilot.application.ports.knowledge_repository import KnowledgeRepositoryPort


class PgVectorKnowledgeRepository(KnowledgeRepositoryPort):
    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    async def upsert(
        self,
        content: str,
        source: str | None,
        embedding: list[float],
    ) -> None:
        with psycopg.connect(self.connection_string) as connection:
            register_vector(connection)

            vector = Vector(embedding)

            connection.execute(
                """
                INSERT INTO knowledge_chunks (content, source, embedding)
                VALUES (%s, %s, %s)
                ON CONFLICT (source) DO UPDATE
                SET content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding
                """,
                (content, source, vector),
            )

    async def search(
        self,
        embedding: list[float],
        limit: int = 3,
    ) -> list[KnowledgeResult]:
        with psycopg.connect(self.connection_string) as connection:
            register_vector(connection)

            query_vector = Vector(embedding)

            rows = connection.execute(
                """
                SELECT
                    content,
                    source,
                    embedding <=> %s AS distance
                FROM knowledge_chunks
                ORDER BY distance
                LIMIT %s
                """,
                (query_vector, limit),
            ).fetchall()

        return [
            KnowledgeResult(
                content=row[0],
                source=row[1],
                distance=float(row[2]),
            )
            for row in rows
        ]