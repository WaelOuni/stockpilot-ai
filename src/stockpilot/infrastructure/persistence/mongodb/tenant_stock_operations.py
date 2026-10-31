"""Internal B3 use cases. No HTTP composition, DDL or implicit provisioning."""

from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

from bson import ObjectId

from stockpilot.application.ports.tenant_orders import TenantOrdersUnavailable
from stockpilot.application.ports.tenant_stock_operations import (
    TenantStockOperationConflict,
    TenantStockResourceNotFound,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.application.services.tenant_csv import RULES_VERSION, parse_tenant_csv
from stockpilot.domain.entities.tenant_order import identifier
from stockpilot.domain.entities.tenant_stock_operation import (
    AdjustTenantStock,
    ArchiveTenantProduct,
    ApplyTenantImport,
    ImportIssue,
    ImportMode,
    OperationStatus,
)
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.infrastructure.persistence.mongodb.tenant_catalogue import (
    _product,
    _stock,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_stock_records import (
    batch,
    batch_proof,
    movement,
    operation,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_unit_of_work import (
    MongoTenantUnitOfWork,
    fingerprint,
)


class MongoTenantStockOperations(MongoTenantUnitOfWork):
    schema_version = 3

    @staticmethod
    def _permission(principal, permission):
        if permission not in principal.permissions:
            raise AuthorizationDenied("Current stock/import permission required.")

    def _receipt(self, key, principal, session, digest=None):
        row = self._operations(key["namespace"]).find_one(key, session=session)
        if row is None:
            return None
        if row["actor_user_id"] != principal.user_id:
            raise TenantStockOperationConflict("Operation key has different content.")
        if digest is not None and row["fingerprint"] != digest:
            raise TenantStockOperationConflict("Operation key has different content.")
        operation(row, principal.organization_id)
        return row

    def _operations(self, namespace):
        # Additive migration: historical stock receipt schemas remain immutable.
        return self.db["b2b_product_archives" if namespace == "archive" else "b2b_stock_operations"]

    def _operate(self, request, context, command, namespace, permission, change):
        principal = self._current(request, context)
        self._permission(principal, permission)
        org = principal.organization_id
        key = {
            "organization_id": org,
            "namespace": namespace,
            "operation_id": command.operation_id,
        }
        digest = fingerprint(
            {
                "organization": org,
                "actor": principal.user_id,
                "command": asdict(command),
            }
        )

        def read(current, session):
            self._permission(current, permission)
            row = self._receipt(key, current, session, digest)
            return operation(row, org) if row is not None else None

        def reconcile():
            return self._transaction(request, context, read)

        def register(current, session):
            self._permission(current, permission)
            old = self._receipt(key, current, session, digest)
            if old is not None:
                return operation(old, org)
            row = {
                **key,
                "actor_user_id": current.user_id,
                "fingerprint": digest,
                "status": "pending",
                "code": "pending",
                "movement_ids": [],
                "import_id": command.import_id if namespace == "import" else None,
                "created_at": self._now(),
                "finished_at": None,
            }
            self._operations(namespace).insert_one(row, session=session)
            return operation(row, org)

        registered = self._transaction(request, context, register, reconcile)
        if registered.status is not OperationStatus.PENDING:
            return registered

        def execute(current, session):
            self._permission(current, permission)
            row = self._receipt(key, current, session, digest)
            if row is None:
                raise TenantOrdersUnavailable(
                    "Durable operation declaration unavailable."
                )
            if row["status"] != "pending":
                return operation(row, org)
            code, ids = change(current, session)
            finished = {
                **row,
                "status": "succeeded" if code == "ok" else "rejected",
                "code": code,
                "movement_ids": ids,
                "finished_at": self._now(),
            }
            updated = self._operations(namespace).update_one(
                {**key, "fingerprint": digest, "status": "pending"},
                {
                    "$set": {
                        k: finished[k]
                        for k in ("status", "code", "movement_ids", "finished_at")
                    }
                },
                session=session,
            )
            if updated.matched_count != 1:
                raise TenantOrdersUnavailable("Operation result changed.")
            return operation(finished, org)

        return self._transaction(request, context, execute, reconcile)

    def _movement(self, principal, session, command, before, after, namespace, source):
        record = {
            "movement_id": uuid4().hex,
            "organization_id": principal.organization_id,
            "product_id": after["_id"],
            "sku": after["sku"],
            "operation_id": command.operation_id,
            "actor_user_id": principal.user_id,
            "operation_namespace": namespace,
            "kind": "ADJUSTMENT" if namespace == "adjust" else "IMPORT_ADJUSTMENT",
            "quantity_before": before["quantity"] if before else 0,
            "quantity_after": after["quantity"],
            "reserved_before": before["reserved"] if before else 0,
            "reserved_after": after["reserved"],
            "revision_after": after["revision"],
            "reason": command.reason if namespace == "adjust" else "CSV physical stock",
            "source_reference": source,
            "occurred_at": self._now(),
        }
        movement(record, principal.organization_id)
        self.db.b2b_stock_movements.insert_one(record, session=session)
        return record["movement_id"]

    def archive(self, request, context, command):
        if not isinstance(command, ArchiveTenantProduct):
            raise TypeError("Typed catalogue removal required.")
        from stockpilot.infrastructure.persistence.mongodb.schema_migrations import MigrationRunner
        MigrationRunner(self.db).require_version(4)

        def change(principal, session):
            org = principal.organization_id
            if not ObjectId.is_valid(command.product_id):
                return "unavailable_resource", []
            key = {"organization_id": org, "_id": ObjectId(command.product_id)}
            row = self.db.b2b_products.find_one(key, session=session)
            if row is None:
                return "unavailable_resource", []
            facts = _stock(row, org)
            _product(row, org)
            if facts.revision != command.expected_revision:
                return "revision_conflict", []
            if facts.reserved:
                return "reserved_conflict", []
            if row["status"] == "inactive":
                return "ok", []
            updated = self.db.b2b_products.update_one(
                {**key, "revision": command.expected_revision, "reserved": 0},
                {"$set": {"status": "inactive"}, "$inc": {"revision": 1}},
                session=session,
            )
            if updated.matched_count != 1:
                raise TenantOrdersUnavailable("Product changed during removal.")
            # Preserve quantities and references from historical orders.
            return "ok", []

        return self._operate(
            request, context, command, "archive", P.IMPORT_EXECUTE, change
        )

    def adjust(self, request, context, command):
        if not isinstance(command, AdjustTenantStock):
            raise TypeError("Typed physical adjustment required.")

        def change(principal, session):
            org = principal.organization_id
            if not ObjectId.is_valid(command.product_id):
                return "unavailable_resource", []
            row = self.db.b2b_products.find_one(
                {"organization_id": org, "_id": ObjectId(command.product_id)},
                session=session,
            )
            if row is None:
                return "unavailable_resource", []
            facts = _stock(row, org)
            _product(row, org)
            if facts.revision != command.expected_revision:
                return "revision_conflict", []
            if facts.reserved > command.quantity:
                return "reserved_conflict", []
            result = self.db.b2b_products.update_one(
                {
                    "organization_id": org,
                    "_id": row["_id"],
                    "sku": row["sku"],
                    "revision": command.expected_revision,
                    "$expr": {"$lte": ["$reserved", command.quantity]},
                },
                {"$set": {"quantity": command.quantity}, "$inc": {"revision": 1}},
                session=session,
            )
            if result.matched_count != 1:
                raise TenantOrdersUnavailable(
                    "Physical stock changed during transaction."
                )
            after = {
                **row,
                "quantity": command.quantity,
                "revision": row["revision"] + 1,
            }
            return "ok", [
                self._movement(
                    principal,
                    session,
                    command,
                    row,
                    after,
                    "adjust",
                    command.operation_id,
                )
            ]

        return self._operate(
            request, context, command, "adjust", P.STOCK_ADJUST, change
        )

    def preview(self, request, context, content, mode):
        current = self._current(request, context)
        self._permission(current, P.IMPORT_EXECUTE)
        parsed = parse_tenant_csv(content, mode)

        def prepare(principal, session):
            self._permission(principal, P.IMPORT_EXECUTE)
            org = principal.organization_id
            rows, issues = [], list(parsed.issues)
            for entry in parsed.rows:
                values = entry["values"]
                before = self.db.b2b_products.find_one(
                    {"organization_id": org, "sku": values["sku"]}, session=session
                )
                if before is not None:
                    _product(before, org)
                if (mode is ImportMode.CREATE_ONLY and before is not None) or (
                    mode is not ImportMode.CREATE_ONLY and before is None
                ):
                    issues.append(
                        ImportIssue(
                            entry["line"],
                            "availability",
                            "SKU unavailable for this import mode.",
                        )
                    )
                elif (
                    mode is ImportMode.STOCK_ONLY
                    and values["quantity"] < before["reserved"]
                ):
                    issues.append(
                        ImportIssue(
                            entry["line"],
                            "reserved",
                            "Physical quantity cannot be below reserved.",
                        )
                    )
                else:
                    rows.append({**entry, "before": before})
            now = self._now()
            row = {
                "import_id": uuid4().hex,
                "organization_id": org,
                "actor_user_id": principal.user_id,
                "session_binding": fingerprint(context.authorization_context_id),
                "mode": mode.value,
                "status": "invalid" if issues else "preview",
                "file_fingerprint": parsed.file_fingerprint,
                "rules_version": RULES_VERSION,
                "rows": [] if issues else rows,
                "issues": [asdict(issue) for issue in issues[:50]],
                "created_at": now,
                "expires_at": now + timedelta(minutes=30),
                "operation_id": None,
                "finished_at": None,
            }
            row["preview_digest"] = batch_proof(row)
            batch(row, org)
            self.db.b2b_imports.insert_one(row, session=session)
            return batch(row, org)

        return self._transaction(request, context, prepare)

    def apply(self, request, context, command):
        if not isinstance(command, ApplyTenantImport):
            raise TypeError("Typed explicit import application required.")

        def change(principal, session):
            org = principal.organization_id
            query = {"organization_id": org, "import_id": command.import_id}
            row = self.db.b2b_imports.find_one(query, session=session)
            if row is None:
                return "unavailable_resource", []
            preview = batch(row, org)
            if (
                row["actor_user_id"] != principal.user_id
                or row["session_binding"]
                != fingerprint(context.authorization_context_id)
                or row["preview_digest"] != command.preview_digest
            ):
                return "preview_invalid", []
            if preview.status not in ("preview", "invalid"):
                return "consumed", []
            if row["expires_at"] <= self._now():
                return "expired", []
            mode = preview.mode
            # Validate the entire plan before the first product/movement write.
            code, plans = ("invalid_csv" if row["status"] == "invalid" else "ok"), []
            for entry in row["rows"]:
                values, before = entry["values"], entry["before"]
                current = self.db.b2b_products.find_one(
                    {"organization_id": org, "sku": values["sku"]}, session=session
                )
                if current is not None:
                    _product(current, org)
                if mode is ImportMode.CREATE_ONLY:
                    if current is not None:
                        code = "revision_conflict"
                elif current is None:
                    code = "unavailable_resource"
                elif (
                    current["_id"] != before["_id"]
                    or current["revision"] != before["revision"]
                ):
                    code = "revision_conflict"
                elif (
                    mode is ImportMode.STOCK_ONLY
                    and values["quantity"] < current["reserved"]
                ):
                    code = "reserved_conflict"
                plans.append((values, current))
            ids = []
            if code == "ok":
                for values, before in plans:
                    if mode is ImportMode.CREATE_ONLY:
                        after = {
                            "_id": ObjectId(),
                            "organization_id": org,
                            **values,
                            "attributes": values.get("attributes", []),
                            "status": "active",
                            "reserved": 0,
                            "revision": 1,
                        }
                        _product(after, org)
                        self.db.b2b_products.insert_one(after, session=session)
                    else:
                        fields = {k: v for k, v in values.items() if k != "sku"}
                        result = self.db.b2b_products.update_one(
                            {
                                "organization_id": org,
                                "_id": before["_id"],
                                "sku": values["sku"],
                                "revision": before["revision"],
                                "$expr": {
                                    "$lte": [
                                        "$reserved",
                                        values["quantity"]
                                        if mode is ImportMode.STOCK_ONLY
                                        else "$quantity",
                                    ]
                                },
                            },
                            {"$set": fields, "$inc": {"revision": 1}},
                            session=session,
                        )
                        if result.matched_count != 1:
                            raise TenantOrdersUnavailable(
                                "Import stock changed during transaction."
                            )
                        after = {**before, **fields, "revision": before["revision"] + 1}
                    if mode is not ImportMode.METADATA_ONLY:
                        ids.append(
                            self._movement(
                                principal,
                                session,
                                command,
                                before,
                                after,
                                "import",
                                command.import_id,
                            )
                        )
            updated = self.db.b2b_imports.update_one(
                {**query, "status": row["status"]},
                {
                    "$set": {
                        "status": "applied" if code == "ok" else "rejected",
                        "operation_id": command.operation_id,
                        "finished_at": self._now(),
                    }
                },
                session=session,
            )
            if updated.matched_count != 1:
                raise TenantOrdersUnavailable("Import lifecycle changed.")
            return code, ids

        return self._operate(
            request, context, command, "import", P.IMPORT_EXECUTE, change
        )

    def result(self, request, context, namespace, operation_id):
        identifier(operation_id)
        if namespace not in ("adjust", "import", "archive"):
            raise ValueError("Known namespace required.")

        def read(principal, session):
            self._permission(
                principal, P.STOCK_ADJUST if namespace == "adjust" else P.IMPORT_EXECUTE
            )
            row = self._operations(namespace).find_one(
                {
                    "organization_id": principal.organization_id,
                    "namespace": namespace,
                    "operation_id": operation_id,
                    "actor_user_id": principal.user_id,
                },
                session=session,
            )
            if row is None:
                raise TenantStockResourceNotFound("Resource unavailable.")
            return operation(row, principal.organization_id)

        return self._transaction(request, context, read)

    @staticmethod
    def _pagination(limit, offset):
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 10000
        ):
            raise ValueError("Bounded pagination required.")

    def movements(self, request, context, limit=20, offset=0):
        self._pagination(limit, offset)

        def read(principal, session):
            self._permission(principal, P.STOCK_READ)
            rows = (
                self.db.b2b_stock_movements.find(
                    {"organization_id": principal.organization_id}, session=session
                )
                .sort([("occurred_at", -1), ("movement_id", -1)])
                .skip(offset)
                .limit(limit)
            )
            return tuple(movement(row, principal.organization_id) for row in rows)

        return self._transaction(request, context, read)

    def imports(self, request, context, limit=20, offset=0):
        self._pagination(limit, offset)

        def read(principal, session):
            self._permission(principal, P.IMPORT_EXECUTE)
            rows = (
                self.db.b2b_imports.find(
                    {"organization_id": principal.organization_id}, session=session
                )
                .sort([("created_at", -1), ("import_id", -1)])
                .skip(offset)
                .limit(limit)
            )
            return tuple(batch(row, principal.organization_id) for row in rows)

        return self._transaction(request, context, read)
