"""Tenant contact persistence. Shared identity fences; no ORDER/RAG calls."""
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

from stockpilot.application.ports.tenant_contact import ContactRateLimited
from stockpilot.application.ports.tenant_knowledge import TenantDataConflict, TenantDataNotFound
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_contact import CreateContact, ReplyContact, SetContactStatus, ContactQuery
from stockpilot.domain.identity_validation import require_identifier
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.infrastructure.persistence.mongodb.tenant_unit_of_work import MongoTenantUnitOfWork, fingerprint

REQUEST_FIELDS = ("request_id", "subject", "category", "description", "created_by", "created_at", "updated_at", "status", "revision")
REPLY_FIELDS = ("reply_id", "request_id", "body", "created_by", "created_at")


class MongoTenantContact(MongoTenantUnitOfWork):
    schema_version = 5

    @staticmethod
    def _permission(principal, permission):
        if permission not in principal.permissions:
            raise AuthorizationDenied("Contact permission required.")

    def _load(self, principal, request_id, session):
        row = self.db.b2b_contact_requests.find_one(
            {"organization_id": principal.organization_id, "request_id": request_id}, session=session)
        if row is None:
            raise TenantDataNotFound("Contact request unavailable.")
        if P.CONTACT_READ_ORGANIZATION not in principal.permissions and not (
            P.CONTACT_READ_OWN in principal.permissions and row["created_by"] == principal.user_id
        ):
            raise TenantDataNotFound("Contact request unavailable.")
        return row

    @staticmethod
    def _public(row, fields):
        return {key: row[key] for key in fields}

    def _quota(self, principal, action, session):
        # Identity/org fences serialize concurrent admissions across workers.
        # Duplicated operation keys are checked before quotas and consume no slot.
        now = self._now()
        query = {"organization_id": principal.organization_id, "action": action,
                 "created_at": {"$gte": now - timedelta(minutes=10 if action == "create" else 1)}}
        user_count = self.db.b2b_contact_operations.count_documents(
            {**query, "user_id": principal.user_id}, session=session)
        limit = 5 if action == "create" else 20 if action == "reply" else 60
        if user_count >= limit:
            raise ContactRateLimited()
        if action == "create":
            query["created_at"] = {"$gte": now - timedelta(hours=1)}
            if self.db.b2b_contact_operations.count_documents(query, session=session) >= 50:
                raise ContactRateLimited()

    @staticmethod
    def _receipt(row):
        return {key: row[key] for key in ("operation_id", "request_id", "reply_id")}

    def _write(self, request, context, command, action, permission, change):
        digest = fingerprint({"action": action, "command": asdict(command)})

        def execute(principal, session):
            self._permission(principal, permission)
            if action != "create":
                self._load(principal, command.request_id, session)
            key = {"organization_id": principal.organization_id, "user_id": principal.user_id,
                   "operation_id": command.operation_id}
            old = self.db.b2b_contact_operations.find_one(key, session=session)
            if old is not None:
                if old["fingerprint"] != digest:
                    raise TenantDataConflict("Contact operation key has different content.")
                self._load(principal, old["request_id"], session)
                return self._receipt(old)
            self._quota(principal, action, session)
            request_id, reply_id = change(principal, session)
            row = {**key, "action": action, "fingerprint": digest, "request_id": request_id,
                   "reply_id": reply_id, "created_at": self._now()}
            self.db.b2b_contact_operations.insert_one(row, session=session)
            return self._receipt(row)

        return self._transaction(request, context, execute)

    def create(self, request, context, command):
        if not isinstance(command, CreateContact):
            raise TypeError("Typed contact request required.")
        def change(principal, session):
            now, request_id = self._now(), uuid4().hex
            self.db.b2b_contact_requests.insert_one({
                "organization_id": principal.organization_id, "created_by": principal.user_id,
                "request_id": request_id, "subject": command.subject, "category": command.category,
                "description": command.description, "status": "open", "created_at": now,
                "updated_at": now, "revision": 1,
            }, session=session)
            return request_id, None
        return self._write(request, context, command, "create", P.CONTACT_CREATE, change)

    def get(self, request, context, request_id):
        require_identifier(request_id)
        def read(principal, session):
            return self._public(self._load(principal, request_id, session), REQUEST_FIELDS)
        return self._transaction(request, context, read)

    def list(self, request, context, query):
        if not isinstance(query, ContactQuery):
            raise TypeError("Typed contact filters required.")
        def read(principal, session):
            self._permission(principal, P.CONTACT_READ_ORGANIZATION if query.scope == "organization" else P.CONTACT_READ_OWN)
            filters = {"organization_id": principal.organization_id}
            if query.scope == "own":
                filters["created_by"] = principal.user_id
            if query.status is not None:
                filters["status"] = query.status
            rows = self.db.b2b_contact_requests.find(filters, session=session).sort(
                [("created_at", -1), ("request_id", -1)]).skip(query.offset).limit(query.limit)
            items = [self._public(row, REQUEST_FIELDS) for row in rows]
            total = self.db.b2b_contact_requests.count_documents(filters, session=session)
            return {"items": items, "total": total, "has_more": query.offset + query.limit < total,
                    "limit": query.limit, "offset": query.offset}
        return self._transaction(request, context, read)

    def replies(self, request, context, request_id, limit=20, offset=0):
        require_identifier(request_id)
        ContactQuery(limit, offset)
        def read(principal, session):
            self._load(principal, request_id, session)
            filters = {"organization_id": principal.organization_id, "request_id": request_id}
            rows = self.db.b2b_contact_replies.find(filters, session=session).sort(
                [("created_at", 1), ("reply_id", 1)]).skip(offset).limit(limit)
            items = [self._public(row, REPLY_FIELDS) for row in rows]
            total = self.db.b2b_contact_replies.count_documents(filters, session=session)
            return {"items": items, "total": total, "has_more": offset + limit < total, "limit": limit, "offset": offset}
        return self._transaction(request, context, read)

    def reply(self, request, context, command):
        if not isinstance(command, ReplyContact):
            raise TypeError("Typed contact reply required.")
        def change(principal, session):
            reply_id, now = uuid4().hex, self._now()
            self.db.b2b_contact_replies.insert_one({
                "organization_id": principal.organization_id, "request_id": command.request_id,
                "reply_id": reply_id, "created_by": principal.user_id, "created_at": now, "body": command.body,
            }, session=session)
            self.db.b2b_contact_requests.update_one(
                {"organization_id": principal.organization_id, "request_id": command.request_id},
                {"$set": {"updated_at": now}, "$inc": {"revision": 1}}, session=session)
            return command.request_id, reply_id
        return self._write(request, context, command, "reply", P.CONTACT_REPLY_ORGANIZATION, change)

    def set_status(self, request, context, command):
        if not isinstance(command, SetContactStatus):
            raise TypeError("Typed contact status required.")
        def change(principal, session):
            self.db.b2b_contact_requests.update_one(
                {"organization_id": principal.organization_id, "request_id": command.request_id, "status": {"$ne": command.status}},
                {"$set": {"status": command.status, "updated_at": self._now()}, "$inc": {"revision": 1}}, session=session)
            return command.request_id, None
        return self._write(request, context, command, "status", P.CONTACT_MANAGE_ORGANIZATION, change)
