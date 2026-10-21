"""Private transcript and write-ahead action journal on the B4 boundary."""

import hashlib
import json

from psycopg import sql
from psycopg.types.json import Jsonb

from stockpilot.application.ports.tenant_knowledge import TenantDataConflict
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.infrastructure.persistence.postgres.tenant_checkpointer import (
    PgTenantConversations,
    identifier,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PgTenantAssistant(PgTenantConversations):
    def conversations_list(self, request, context, limit=20, offset=0):
        if (
            type(limit) is not int
            or not 1 <= limit <= 50
            or type(offset) is not int
            or not 0 <= offset <= 10000
        ):
            raise ValueError("Bounded pagination required.")
        with self.connection(request, context, P.CONVERSATION_OWN) as c:
            rows = c.execute(
                sql.SQL(
                    "SELECT conversation_id,created_at FROM {} WHERE organization_id=%s "
                    "AND owner_user_id=%s AND status='active' ORDER BY created_at DESC,conversation_id LIMIT %s OFFSET %s"
                ).format(self.t("conversations")),
                (context.organization_id, context.principal.user_id, limit, offset),
            ).fetchall()
            return [
                {"conversation_id": row[0], "created_at": row[1].isoformat()}
                for row in rows
            ]

    def _key(self, context, conversation_id):
        return (
            context.organization_id,
            context.principal.user_id,
            identifier(conversation_id),
        )

    def turn(self, request, context, conversation_id, turn_id):
        identifier(turn_id)
        with self.connection(request, context, P.CONVERSATION_OWN) as c:
            self._owned(c, context, conversation_id)
            row = c.execute(
                sql.SQL(
                    "SELECT turn_id,kind,input,action,reply,status,created_at FROM {} "
                    "WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s AND turn_id=%s"
                ).format(self.t("assistant_turns")),
                (*self._key(context, conversation_id), turn_id),
            ).fetchone()
            return self._view(row) if row else None

    @staticmethod
    def _view(row):
        return dict(
            zip(
                ("turn_id", "kind", "input", "action", "reply", "status", "created_at"),
                (*row[:6], row[6].isoformat()),
                strict=True,
            )
        )

    def history(self, request, context, conversation_id, limit=20, offset=0):
        if (
            type(limit) is not int
            or not 1 <= limit <= 20
            or type(offset) is not int
            or not 0 <= offset <= 100
        ):
            raise ValueError("Bounded transcript required.")
        with self.connection(request, context, P.CONVERSATION_OWN) as c:
            self._owned(c, context, conversation_id)
            rows = c.execute(
                sql.SQL(
                    "SELECT turn_id,kind,input,action,reply,status,created_at FROM {} "
                    "WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s "
                    "ORDER BY created_at DESC,turn_id DESC LIMIT %s OFFSET %s"
                ).format(self.t("assistant_turns")),
                (*self._key(context, conversation_id), limit + 1, offset),
            ).fetchall()
            return {
                "items": [self._view(r) for r in reversed(rows[:limit])],
                "has_more": len(rows) > limit,
                "limit": limit,
                "offset": offset,
            }

    def begin(self, request, context, conversation_id, turn_id, kind, value):
        identifier(turn_id)
        if (
            kind not in {"message", "confirm", "cancel"}
            or len(canonical(value).encode()) > 20000
        ):
            raise ValueError("Invalid bounded turn.")
        proof = hashlib.sha256(canonical([kind, value]).encode()).hexdigest()
        with self.connection(request, context, P.CONVERSATION_OWN, write=True) as c:
            self._owned(c, context, conversation_id)
            old = c.execute(
                sql.SQL(
                    "SELECT fingerprint FROM {} WHERE organization_id=%s AND owner_user_id=%s "
                    "AND conversation_id=%s AND turn_id=%s"
                ).format(self.t("assistant_turns")),
                (*self._key(context, conversation_id), turn_id),
            ).fetchone()
            if old:
                if old[0] != proof:
                    raise TenantDataConflict("Turn id/content conflict.")
                return
            pending = c.execute(
                sql.SQL(
                    "SELECT count(*),count(*) FILTER (WHERE status='pending') FROM {} "
                    "WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s"
                ).format(self.t("assistant_turns")),
                self._key(context, conversation_id),
            ).fetchone()
            if pending[1]:
                raise TenantDataConflict(
                    "Reconcile the pending turn before another action."
                )
            if pending[0] >= 100:
                raise TenantDataConflict(
                    "Conversation limit reached; create another conversation."
                )
            c.execute(
                sql.SQL(
                    "INSERT INTO {} (organization_id,owner_user_id,conversation_id,turn_id,fingerprint,kind,input) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)"
                ).format(self.t("assistant_turns")),
                (
                    *self._key(context, conversation_id),
                    turn_id,
                    proof,
                    kind,
                    Jsonb(value),
                ),
            )

    def plan(self, request, context, conversation_id, turn_id, action):
        # This commit precedes every Mongo write. A retry must reuse this exact plan.
        if len(canonical(action).encode()) > 20000:
            raise ValueError("Action too large.")
        with self.connection(request, context, P.CONVERSATION_OWN, write=True) as c:
            self._owned(c, context, conversation_id)
            old = c.execute(
                sql.SQL(
                    "SELECT action FROM {} WHERE organization_id=%s AND owner_user_id=%s "
                    "AND conversation_id=%s AND turn_id=%s FOR UPDATE"
                ).format(self.t("assistant_turns")),
                (*self._key(context, conversation_id), identifier(turn_id)),
            ).fetchone()
            if old is None or (old[0] is not None and old[0] != action):
                raise TenantDataConflict("Original action plan required.")
            c.execute(
                sql.SQL(
                    "UPDATE {} SET action=%s WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s AND turn_id=%s"
                ).format(self.t("assistant_turns")),
                (Jsonb(action), *self._key(context, conversation_id), turn_id),
            )

    def finish(self, request, context, conversation_id, turn_id, reply):
        if len(canonical(reply).encode()) > 65536:
            raise ValueError("Reply too large.")
        with self.connection(request, context, P.CONVERSATION_OWN, write=True) as c:
            self._owned(c, context, conversation_id)
            c.execute(
                sql.SQL(
                    "UPDATE {} SET reply=%s,status='completed' WHERE organization_id=%s AND owner_user_id=%s "
                    "AND conversation_id=%s AND turn_id=%s"
                ).format(self.t("assistant_turns")),
                (
                    Jsonb(reply),
                    *self._key(context, conversation_id),
                    identifier(turn_id),
                ),
            )

    def close(self, request, context, conversation_id):
        with self.connection(request, context, P.CONVERSATION_OWN, write=True) as c:
            self._execution_lock(c, context, conversation_id)
            self._owned(c, context, conversation_id)
            pending = c.execute(
                sql.SQL(
                    "SELECT 1 FROM {} WHERE organization_id=%s AND owner_user_id=%s "
                    "AND conversation_id=%s AND status='pending' LIMIT 1"
                ).format(self.t("assistant_turns")),
                self._key(context, conversation_id),
            ).fetchone()
            if pending:
                raise TenantDataConflict(
                    "Pending operation must be reconciled before closure."
                )
            c.execute(
                sql.SQL(
                    "UPDATE {} SET status='deleted' WHERE organization_id=%s AND owner_user_id=%s AND conversation_id=%s"
                ).format(self.t("conversations")),
                self._key(context, conversation_id),
            )
