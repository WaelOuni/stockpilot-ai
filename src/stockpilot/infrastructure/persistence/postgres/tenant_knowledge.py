"""Exact, SQL-scoped B2B retrieval and atomic version publication."""

import hashlib
import json
from dataclasses import asdict
from uuid import uuid4

from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg import sql

from stockpilot.application.ports.tenant_knowledge import (
    TenantDataConflict,
    TenantDataNotFound,
    TenantDataUnavailable,
)
from stockpilot.domain.entities.tenant_knowledge import (
    IngestionResult,
    IngestTenantDocument,
    TenantDocument,
    TenantPassage,
    vector,
)
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.domain.identity_validation import require_identifier
from stockpilot.infrastructure.persistence.postgres.tenant_boundary import (
    TenantPostgresBoundary,
)
from stockpilot.application.services.tenant_rag_safety import contains_sensitive


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PgTenantKnowledge(TenantPostgresBoundary):
    def __init__(
        self, uri, authorization, embeddings, schema="stockpilot_b2b", *, model, version
    ):
        super().__init__(uri, authorization, schema)
        self.embeddings = embeddings
        require_identifier(model)
        require_identifier(version)
        self.model, self.version = model, version

    def documents_by_title(self, request, context, title):
        from stockpilot.domain.entities.tenant_knowledge import text

        text(title, 200)
        with self.connection(request, context, P.RAG_READ) as c:
            rows = c.execute(
                sql.SQL(
                    "SELECT document_id,title,revision,status,embedding_model,embedding_version,active_generation FROM {} "
                    "WHERE organization_id=%s AND title=%s AND status='ready' ORDER BY document_id LIMIT 20"
                ).format(self.t("documents")),
                (context.organization_id, title),
            ).fetchall()
            return [TenantDocument(context.organization_id, *r[:6]) for r in rows
                    if self._safe_document(c, context.organization_id, r[0], r[1], r[6], *r[4:6])]

    def _safe_document(self, connection, org, document_id, title, generation, *metadata):
        """Classify the complete bounded generation, including legacy metadata."""
        if any(contains_sensitive(value) for value in (document_id, title, *metadata)):
            return False
        if generation is None:
            return True
        chunks = connection.execute(sql.SQL(
            'SELECT content FROM {} WHERE organization_id=%s AND document_id=%s AND generation_id=%s ORDER BY chunk_id LIMIT 33'
        ).format(self.t('chunks')), (org, document_id, generation)).fetchall()
        content = ''.join(chunk[0] for chunk in chunks)
        return len(chunks) <= 32 and len(content) <= 64000 and not contains_sensitive(content)

    def _document(self, c, org, document_id, *, lock=False):
        row = c.execute(
            sql.SQL(
                "SELECT title,revision,status,embedding_model,embedding_version,active_generation "
                "FROM {} WHERE organization_id=%s AND document_id=%s"
                + (" FOR UPDATE" if lock else "")
            ).format(self.t("documents")),
            (org, document_id),
        ).fetchone()
        if row is None:
            raise TenantDataNotFound("Document unavailable.")
        return row

    def document(self, request, context, document_id):
        require_identifier(document_id)
        with self.connection(request, context, P.RAG_READ) as c:
            row = self._document(c, context.organization_id, document_id)
            if not self._safe_document(c, context.organization_id, document_id, row[0], row[5], *row[3:5]):
                raise TenantDataNotFound('Document unavailable.')
            return TenantDocument(context.organization_id, document_id, *row[:5])

    def _begin(self, request, context, command, proof):
        org, user = context.organization_id, context.principal.user_id
        with self.connection(request, context, P.RAG_MANAGE, write=True) as c:
            c.execute(
                sql.SQL(
                    "INSERT INTO {} (organization_id,document_id,title,revision,status,embedding_model,embedding_version) "
                    "VALUES (%s,%s,%s,0,'pending',%s,%s) ON CONFLICT DO NOTHING"
                ).format(self.t("documents")),
                (
                    org,
                    command.document_id,
                    command.title,
                    command.embedding_model,
                    command.embedding_version,
                ),
            )
            row = self._document(c, org, command.document_id, lock=True)
            operation = c.execute(
                sql.SQL(
                    "SELECT actor_user_id,fingerprint,generation_id,status,revision FROM {} "
                    "WHERE organization_id=%s AND operation_id=%s FOR UPDATE"
                ).format(self.t("ingestions")),
                (org, command.operation_id),
            ).fetchone()
            if operation:
                if operation[:2] != (user, proof):
                    raise TenantDataConflict("Ingestion key conflict.")
                if operation[3] == "ready":
                    return operation[2], IngestionResult(
                        command.operation_id, command.document_id, "ready", operation[4]
                    )
                generation = operation[2]
                c.execute(
                    sql.SQL(
                        "UPDATE {} SET status='pending' WHERE organization_id=%s AND operation_id=%s"
                    ).format(self.t("ingestions")),
                    (org, command.operation_id),
                )
            else:
                generation = uuid4().hex
                c.execute(
                    sql.SQL(
                        "INSERT INTO {} (organization_id,operation_id,document_id,actor_user_id,fingerprint,"
                        "generation_id,status,revision) VALUES (%s,%s,%s,%s,%s,%s,'pending',%s)"
                    ).format(self.t("ingestions")),
                    (
                        org,
                        command.operation_id,
                        command.document_id,
                        user,
                        proof,
                        generation,
                        command.expected_revision + 1,
                    ),
                )
            if row[1] != command.expected_revision:
                raise TenantDataConflict("Document revision changed.")
            return generation, None

    def _fail(self, request, context, command):
        with self.connection(request, context, P.RAG_MANAGE, write=True) as c:
            c.execute(
                sql.SQL(
                    "UPDATE {} SET status='failed' WHERE organization_id=%s AND operation_id=%s "
                    "AND status='pending'"
                ).format(self.t("ingestions")),
                (context.organization_id, command.operation_id),
            )
            c.execute(
                sql.SQL(
                    "UPDATE {} SET status='failed',updated_at=now() WHERE organization_id=%s "
                    "AND document_id=%s AND active_generation IS NULL AND status='pending'"
                ).format(self.t("documents")),
                (context.organization_id, command.document_id),
            )

    async def ingest(self, request, context, command):
        self.current(request, context, P.RAG_MANAGE)
        if not isinstance(command, IngestTenantDocument):
            raise TypeError("Typed ingestion command required.")
        if (command.embedding_model, command.embedding_version) != (
            self.model,
            self.version,
        ):
            raise TenantDataConflict("Server embedding configuration mismatch.")
        if any(contains_sensitive(value) for value in (
            command.operation_id, command.document_id, command.title, command.content,
            command.embedding_model, command.embedding_version,
        )):
            raise TenantDataConflict('Document contains credential-like material; remove it before ingestion.')
        proof = digest({"actor": context.principal.user_id, **asdict(command)})
        generation, replay = self._begin(request, context, command, proof)
        if replay:
            return replay
        # Deterministic bounded chunks. No partial chunk is visible during embedding.
        chunks = [
            command.content[i : i + 2000] for i in range(0, len(command.content), 2000)
        ]
        try:
            embeddings = [
                vector(await self.embeddings.embed(chunk)) for chunk in chunks
            ]
        except Exception:
            self._fail(request, context, command)
            raise TenantDataUnavailable(
                "Embedding generation failed; operation retained."
            ) from None
        org = context.organization_id
        try:
            with self.connection(request, context, P.RAG_MANAGE, write=True) as c:
                row = self._document(c, org, command.document_id, lock=True)
                op = c.execute(
                    sql.SQL(
                        "SELECT status,revision FROM {} WHERE organization_id=%s AND operation_id=%s FOR UPDATE"
                    ).format(self.t("ingestions")),
                    (org, command.operation_id),
                ).fetchone()
                if op and op[0] == "ready":
                    return IngestionResult(
                        command.operation_id, command.document_id, "ready", op[1]
                    )
                if not op or row[1] != command.expected_revision:
                    raise TenantDataConflict("Document revision changed.")
                register_vector(c)
                for number, (chunk, embedding) in enumerate(
                    zip(chunks, embeddings, strict=True)
                ):
                    c.execute(
                        sql.SQL(
                            "INSERT INTO {} (organization_id,document_id,generation_id,chunk_id,content,embedding) "
                            "VALUES (%s,%s,%s,%s,%s,%s)"
                        ).format(self.t("chunks")),
                        (
                            org,
                            command.document_id,
                            generation,
                            number,
                            chunk,
                            Vector(list(embedding)),
                        ),
                    )
                c.execute(
                    sql.SQL(
                        "UPDATE {} SET title=%s,revision=revision+1,status='ready',active_generation=%s,"
                        "embedding_model=%s,embedding_version=%s,updated_at=now() "
                        "WHERE organization_id=%s AND document_id=%s AND revision=%s"
                    ).format(self.t("documents")),
                    (
                        command.title,
                        generation,
                        command.embedding_model,
                        command.embedding_version,
                        org,
                        command.document_id,
                        command.expected_revision,
                    ),
                )
                c.execute(
                    sql.SQL(
                        "UPDATE {} SET status='ready' WHERE organization_id=%s AND operation_id=%s"
                    ).format(self.t("ingestions")),
                    (org, command.operation_id),
                )
            return IngestionResult(
                command.operation_id,
                command.document_id,
                "ready",
                command.expected_revision + 1,
            )
        except TenantDataConflict:
            self._fail(request, context, command)
            raise

    def ingestion(self, request, context, operation_id):
        require_identifier(operation_id)
        with self.connection(request, context, P.RAG_MANAGE) as c:
            row = c.execute(
                sql.SQL(
                    "SELECT document_id,status,revision FROM {} WHERE organization_id=%s AND operation_id=%s"
                ).format(self.t("ingestions")),
                (context.organization_id, operation_id),
            ).fetchone()
            if not row:
                raise TenantDataNotFound("Ingestion unavailable.")
            document = self._document(c, context.organization_id, row[0])
            if contains_sensitive(operation_id) or not self._safe_document(
                c, context.organization_id, row[0], document[0], document[5], *document[3:5]
            ):
                raise TenantDataNotFound('Ingestion unavailable.')
            return IngestionResult(operation_id, *row)

    def search(self, request, context, embedding, *, model, version, limit=3):
        embedding = vector(embedding)
        require_identifier(model)
        require_identifier(version)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Bounded result limit required.")
        with self.connection(request, context, P.RAG_READ) as c:
            org = context.organization_id
            register_vector(c)
            # MATERIALIZED enforces tenant/active/model scope before exact distance sort.
            rows = c.execute(
                sql.SQL(
                    "WITH scoped AS MATERIALIZED (SELECT d.organization_id,d.document_id,k.chunk_id,k.content,k.embedding,d.title,k.generation_id "
                    "FROM {} d JOIN {} k ON k.organization_id=d.organization_id AND k.document_id=d.document_id "
                    "AND k.generation_id=d.active_generation JOIN {} i ON i.organization_id=k.organization_id "
                    "AND i.document_id=k.document_id AND i.generation_id=k.generation_id "
                    "WHERE d.organization_id=%s AND d.status='ready' AND i.status='ready' "
                    "AND d.embedding_model=%s AND d.embedding_version=%s) "
                    "SELECT organization_id,document_id,chunk_id,content,embedding <=> %s AS distance,title,generation_id "
                    "FROM scoped ORDER BY distance,document_id,chunk_id LIMIT %s"
                ).format(self.t("documents"), self.t("chunks"), self.t("ingestions")),
                (org, model, version, Vector(list(embedding)), limit),
            ).fetchall()
            # Existing documents may predate the ingestion guard. Classify the
            # complete exact generation, not just a matching chunk: a label and
            # its value can straddle the deterministic 2000-character boundary.
            allowed = {}
            for row in rows:
                key = row[1], row[6]
                if key not in allowed:
                    allowed[key] = self._safe_document(c, org, row[1], row[5], row[6])
            return [TenantPassage(*r[:5], generation_id=r[6]) for r in rows if allowed[(r[1], r[6])]]

    def cited_passage(self, request, context, document_id, generation_id, chunk_id):
        """Re-read an exact still-published source, never a semantic substitute."""
        for value in (document_id, generation_id):
            require_identifier(value)
            if len(value) > 128 or '\x00' in value:
                raise ValueError('Bounded citation identifier required.')
        if type(chunk_id) is not int or not 0 <= chunk_id < 32:
            raise ValueError('Bounded passage identifier required.')
        with self.connection(request, context, P.RAG_READ) as c:
            row = c.execute(sql.SQL(
                'SELECT d.title,d.embedding_model,d.embedding_version,k.content '
                'FROM {} d JOIN {} k ON k.organization_id=d.organization_id '
                'AND k.document_id=d.document_id AND k.generation_id=d.active_generation '
                'JOIN {} i ON i.organization_id=k.organization_id '
                'AND i.document_id=k.document_id AND i.generation_id=k.generation_id '
                "WHERE d.organization_id=%s AND d.document_id=%s AND d.status='ready' "
                "AND i.status='ready' AND k.generation_id=%s AND k.chunk_id=%s "
                'AND d.embedding_model=%s AND d.embedding_version=%s'
            ).format(self.t('documents'), self.t('chunks'), self.t('ingestions')),
                (context.organization_id, document_id, generation_id, chunk_id,
                 self.model, self.version)).fetchone()
            if row is None or not self._safe_document(
                c, context.organization_id, document_id, row[0], generation_id, *row[1:3]
            ):
                return None
            return TenantPassage(context.organization_id, document_id, chunk_id,
                                 row[3], 0.0, generation_id)

    def delete(self, request, context, document_id, *, operation_id, expected_revision):
        self.current(request, context, P.RAG_MANAGE)
        for v in (document_id, operation_id):
            require_identifier(v)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Positive revision required.")
        org, user = context.organization_id, context.principal.user_id
        proof = digest([user, document_id, expected_revision])
        with self.connection(request, context, P.RAG_MANAGE, write=True) as c:
            self._document(c, org, document_id, lock=True)
            old = c.execute(
                sql.SQL(
                    "SELECT fingerprint FROM {} WHERE organization_id=%s AND operation_id=%s"
                ).format(self.t("document_deletions")),
                (org, operation_id),
            ).fetchone()
            if old:
                if old[0] != proof:
                    raise TenantDataConflict("Deletion key conflict.")
                return
            result = c.execute(
                sql.SQL(
                    "UPDATE {} SET status='deleted',active_generation=NULL,revision=revision+1,updated_at=now() "
                    "WHERE organization_id=%s AND document_id=%s AND revision=%s"
                ).format(self.t("documents")),
                (org, document_id, expected_revision),
            )
            if result.rowcount != 1:
                raise TenantDataConflict("Document revision changed.")
            c.execute(
                sql.SQL(
                    "DELETE FROM {} WHERE organization_id=%s AND document_id=%s"
                ).format(self.t("chunks")),
                (org, document_id),
            )
            c.execute(
                sql.SQL(
                    "INSERT INTO {} (organization_id,operation_id,document_id,actor_user_id,fingerprint) "
                    "VALUES (%s,%s,%s,%s,%s)"
                ).format(self.t("document_deletions")),
                (org, operation_id, document_id, user, proof),
            )
