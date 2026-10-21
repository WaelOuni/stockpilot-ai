"""Dedicated root-graph saver, not a wrapper exposing a global saver."""

import asyncio
from contextlib import contextmanager
from uuid import uuid4

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Interrupt
from psycopg import sql

from stockpilot.application.ports.tenant_knowledge import (
    TenantDataConflict,
    TenantDataNotFound,
    TenantDataUnavailable,
)
from stockpilot.domain.entities.tenant_knowledge import TenantConversation
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.domain.identity_validation import require_identifier
from stockpilot.infrastructure.persistence.postgres.tenant_boundary import (
    TenantPostgresBoundary,
)


def identifier(value):
    require_identifier(value)
    if len(value) > 128 or "\x00" in value:
        raise ValueError("Bounded identifier required.")
    return value


class PgTenantConversations(TenantPostgresBoundary):
    def _execution_lock(self, c, context, conversation_id):
        key = repr(
            (context.organization_id, context.principal.user_id, conversation_id)
        )
        if not c.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,7))", (key,)
        ).fetchone()[0]:
            raise TenantDataConflict("Conversation execution in progress.")

    @contextmanager
    def execution(self, request, context, conversation_id):
        with self.connection(request, context, P.CONVERSATION_OWN) as c:
            self._execution_lock(c, context, conversation_id)
            row = c.execute(
                sql.SQL(
                    "SELECT 1 FROM {} WHERE organization_id=%s AND owner_user_id=%s "
                    "AND conversation_id=%s AND status='active'"
                ).format(self.t("conversations")),
                (context.organization_id, context.principal.user_id, conversation_id),
            ).fetchone()
            if not row:
                raise TenantDataNotFound("Conversation unavailable.")
            yield

    def _owned(self, c, context, conversation_id):
        row = c.execute(
            sql.SQL(
                "SELECT external_thread_id FROM {} WHERE organization_id=%s "
                "AND owner_user_id=%s AND conversation_id=%s AND status='active' FOR UPDATE"
            ).format(self.t("conversations")),
            (context.organization_id, context.principal.user_id, conversation_id),
        ).fetchone()
        if not row:
            raise TenantDataNotFound("Conversation unavailable.")
        return row[0]

    def open(self, request, context, external_thread_id):
        identifier(external_thread_id)
        self.current(request, context, P.CONVERSATION_OWN)
        org, user = context.organization_id, context.principal.user_id
        with self.connection(request, context, P.CONVERSATION_OWN, write=True) as c:
            c.execute(
                sql.SQL(
                    "INSERT INTO {} (organization_id,owner_user_id,conversation_id,external_thread_id) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING"
                ).format(self.t("conversations")),
                (org, user, uuid4().hex, external_thread_id),
            )
            row = c.execute(
                sql.SQL(
                    "SELECT conversation_id,status FROM {} WHERE organization_id=%s "
                    "AND owner_user_id=%s AND external_thread_id=%s FOR UPDATE"
                ).format(self.t("conversations")),
                (org, user, external_thread_id),
            ).fetchone()
            if not row or row[1] != "active":
                raise TenantDataNotFound("Conversation unavailable.")
            return TenantConversation(org, user, row[0], external_thread_id)

    def bind(self, request, context, conversation_id):
        identifier(conversation_id)
        with self.connection(request, context, P.CONVERSATION_OWN) as c:
            self._owned(c, context, conversation_id)
        return PgTenantCheckpointSaver(self, request, context, conversation_id)

    def delete(self, request, context, conversation_id):
        identifier(conversation_id)
        with self.connection(request, context, P.CONVERSATION_OWN, write=True) as c:
            self._execution_lock(c, context, conversation_id)
            self._owned(c, context, conversation_id)
            params = (
                context.organization_id,
                context.principal.user_id,
                conversation_id,
            )
            c.execute(
                sql.SQL(
                    "DELETE FROM {} WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s"
                ).format(self.t("checkpoint_writes")),
                params,
            )
            c.execute(
                sql.SQL(
                    "DELETE FROM {} WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s"
                ).format(self.t("checkpoints")),
                params,
            )
            c.execute(
                sql.SQL(
                    "UPDATE {} SET status='deleted' WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s"
                ).format(self.t("conversations")),
                params,
            )


class PgTenantCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, conversations, request, context, conversation_id):
        super().__init__(
            serde=JsonPlusSerializer(
                pickle_fallback=False,
                allowed_json_modules=[],
                allowed_msgpack_modules=[(Interrupt.__module__, Interrupt.__name__)],
            )
        )
        self.conversations, self.request, self.context, self.conversation_id = (
            conversations,
            request,
            context,
            conversation_id,
        )

    def config(self, checkpoint_id=None):
        c = {"thread_id": self.conversation_id, "checkpoint_ns": ""}
        if checkpoint_id is not None:
            c["checkpoint_id"] = checkpoint_id
        return {"configurable": c}

    def _config(self, config):
        values = config.get("configurable", {}) if isinstance(config, dict) else {}
        if (
            values.get("thread_id") != self.conversation_id
            or values.get("checkpoint_ns", "") != ""
        ):
            raise TenantDataNotFound("Conversation unavailable.")
        if any(k in values for k in ("organization_id", "user_id", "customer_id")):
            raise ValueError("Identity cannot be supplied in graph configuration.")
        checkpoint_id = values.get("checkpoint_id")
        if checkpoint_id is not None:
            identifier(checkpoint_id)
        return checkpoint_id

    def _params(self):
        return (
            self.context.organization_id,
            self.context.principal.user_id,
            self.conversation_id,
        )

    def _connection(self, *, write=False):
        return self.conversations.connection(
            self.request, self.context, P.CONVERSATION_OWN, write=write
        )

    def _owned(self, c):
        self.conversations._owned(c, self.context, self.conversation_id)

    def _get(self, c, checkpoint_id=None):
        rows = c.execute(
            sql.SQL(
                "SELECT checkpoint_id,parent_id,checkpoint_type,checkpoint,metadata_type,metadata FROM {} "
                "WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s "
                "AND (%s::text IS NULL OR checkpoint_id=%s) ORDER BY checkpoint_id DESC LIMIT 1"
            ).format(self.conversations.t("checkpoints")),
            (*self._params(), checkpoint_id, checkpoint_id),
        ).fetchone()
        if not rows:
            return None
        cid, parent, kind, data, mkind, metadata = rows
        try:
            checkpoint = self.serde.loads_typed((kind, bytes(data)))
            meta = self.serde.loads_typed((mkind, bytes(metadata)))
            if (
                not isinstance(checkpoint, dict)
                or checkpoint.get("id") != cid
                or not isinstance(meta, dict)
            ):
                raise ValueError("Corrupt checkpoint.")
            writes = c.execute(
                sql.SQL(
                    "SELECT task_id,channel,value_type,value FROM {} WHERE organization_id=%s "
                    "AND owner_user_id=%s AND conversation_id=%s AND checkpoint_id=%s ORDER BY task_id,idx"
                ).format(self.conversations.t("checkpoint_writes")),
                (*self._params(), cid),
            ).fetchall()
            pending = [
                (task, channel, self.serde.loads_typed((t, bytes(v))))
                for task, channel, t, v in writes
            ]
            return CheckpointTuple(
                self.config(cid),
                checkpoint,
                meta,
                self.config(parent) if parent else None,
                pending,
            )
        except (ValueError, TypeError, KeyError, NotImplementedError):
            raise TenantDataUnavailable("Invalid persisted checkpoint.") from None

    def get_tuple(self, config):
        cid = self._config(config)
        with self._connection() as c:
            self._owned(c)
            return self._get(c, cid)

    def list(self, config, *, filter=None, before=None, limit=None):
        cid = self._config(config)
        prior = self._config(before) if before is not None else None
        if filter:
            raise ValueError("Metadata filters not supported by this bounded saver.")
        if limit is None:
            limit = 100
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("History limit must be 1..100.")
        with self._connection() as c:
            self._owned(c)
            rows = c.execute(
                sql.SQL(
                    "SELECT checkpoint_id FROM {} WHERE organization_id=%s AND owner_user_id=%s "
                    "AND conversation_id=%s AND (%s::text IS NULL OR checkpoint_id=%s) "
                    "AND (%s::text IS NULL OR checkpoint_id<%s) ORDER BY checkpoint_id DESC LIMIT %s"
                ).format(self.conversations.t("checkpoints")),
                (*self._params(), cid, cid, prior, prior, limit),
            ).fetchall()
            result = [self._get(c, r[0]) for r in rows]
        # Materialize under the authorized transaction; do not keep a cursor/global iterator.
        return iter(result)

    def put(self, config, checkpoint, metadata, new_versions):
        parent = self._config(config)
        cid = identifier(checkpoint["id"])
        kind, data = self.serde.dumps_typed(checkpoint)
        mkind, meta = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        if len(data) > 1048576 or len(meta) > 65536:
            raise ValueError("Checkpoint too large.")
        with self._connection(write=True) as c:
            self._owned(c)
            if parent and self._get(c, parent) is None:
                raise TenantDataNotFound("Parent checkpoint unavailable.")
            c.execute(
                sql.SQL(
                    "INSERT INTO {} (organization_id,owner_user_id,conversation_id,checkpoint_id,parent_id,"
                    "checkpoint_type,checkpoint,metadata_type,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING"
                ).format(self.conversations.t("checkpoints")),
                (*self._params(), cid, parent, kind, data, mkind, meta),
            )
            actual = c.execute(
                sql.SQL(
                    "SELECT parent_id,checkpoint_type,checkpoint,metadata_type,metadata FROM {} "
                    "WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s AND checkpoint_id=%s"
                ).format(self.conversations.t("checkpoints")),
                (*self._params(), cid),
            ).fetchone()
            if actual != (parent, kind, data, mkind, meta):
                raise TenantDataConflict("Checkpoint identity collision.")
        return self.config(cid)

    def put_writes(self, config, writes, task_id, task_path=""):
        cid = self._config(config)
        identifier(task_id)
        if cid is None or len(writes) > 1000:
            raise ValueError("Checkpoint identifier and bounded writes required.")
        with self._connection(write=True) as c:
            self._owned(c)
            # LangGraph may submit pending writes before its background checkpoint
            # insert completes. Ownership is persisted on the conversation; these
            # writes are exposed only when the exact checkpoint is also present.
            for number, (channel, value) in enumerate(writes):
                identifier(channel)
                idx = WRITES_IDX_MAP.get(channel, number)
                kind, data = self.serde.dumps_typed(value)
                if len(data) > 1048576:
                    raise ValueError("Checkpoint write too large.")
                conflict = (
                    "DO UPDATE SET channel=EXCLUDED.channel,value_type=EXCLUDED.value_type,value=EXCLUDED.value"
                    if idx < 0
                    else "DO NOTHING"
                )
                c.execute(
                    sql.SQL(
                        "INSERT INTO {} (organization_id,owner_user_id,conversation_id,checkpoint_id,"
                        "task_id,idx,channel,value_type,value) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (organization_id,owner_user_id,conversation_id,checkpoint_id,task_id,idx) "
                        + conflict
                    ).format(self.conversations.t("checkpoint_writes")),
                    (*self._params(), cid, task_id, idx, channel, kind, data),
                )

    def delete_thread(self, thread_id):
        self._config({"configurable": {"thread_id": thread_id}})
        self.conversations.delete(self.request, self.context, self.conversation_id)

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for row in rows:
            self.conversations.current(self.request, self.context, P.CONVERSATION_OWN)
            yield row

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(
            self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(
            self.put_writes, config, writes, task_id, task_path
        )

    async def adelete_thread(self, thread_id):
        return await asyncio.to_thread(self.delete_thread, thread_id)
