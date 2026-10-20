"""Explicit, uncomposed B2B ORDER use cases on a single Mongo replica set.

No historical repository is imported. No DDL, provisioning or I/O at import.
"""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bson import ObjectId
from bson.codec_options import CodecOptions
from pymongo import ReadPreference, timeout
from pymongo.errors import ConnectionFailure, DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.ports.tenant_authorization import (
    BuyerAssociationUnavailable,
    TenantContextUnavailable,
)
from stockpilot.application.ports.tenant_orders import (
    TenantOrderConflict,
    TenantOrderNotFound,
    TenantOrderOutcomeUnknown,
    TenantOrdersUnavailable,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_authorization import (
    BuyerAssociation,
    TenantContext,
)
from stockpilot.domain.entities.tenant_order import (
    CancelTenantOrder,
    ConfirmTenantOrder,
    PrepareTenantOrder,
    RequestedLine,
    TenantOrder,
    TenantOrderLine,
    TenantOrderStatus,
    identifier,
)
from stockpilot.domain.enums.permission import Permission as P
from stockpilot.domain.enums.tenant_authorization import BuyerAssociationStatus
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationError,
    MigrationRunner,
    _database,
)
from stockpilot.infrastructure.persistence.mongodb.tenant_catalogue import (
    _integer,
    _product,
    _stock,
)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _proof(row):
    return fingerprint(
        {
            k: row[k]
            for k in (
                "organization_id",
                "order_id",
                "customer_id",
                "created_by",
                "lines",
                "operation_id",
                "session_binding",
                "created_at",
                "expires_at",
            )
        }
    )


def _decode(row, org):
    if row["organization_id"] != org:
        raise ValueError("Invalid persisted order scope.")
    for key in ("order_id", "customer_id", "created_by", "operation_id"):
        identifier(row[key])
    if _integer(row["revision"]) < 1 or row["preparation_digest"] != _proof(row):
        raise ValueError("Invalid persisted preparation proof.")
    lines = []
    for line in row["lines"]:
        RequestedLine(line["sku"], _integer(line["quantity"]))
        if (
            not isinstance(line["product_id"], ObjectId)
            or _integer(line["price_minor"]) < 0
        ):
            raise ValueError("Invalid persisted order facts.")
        if (
            not isinstance(line["currency"], str)
            or len(line["currency"]) != 3
            or not line["currency"].isascii()
            or not line["currency"].isalpha()
            or not line["currency"].isupper()
        ):
            raise ValueError("Invalid currency.")
        lines.append(
            TenantOrderLine(
                str(line["product_id"]),
                line["sku"],
                int(line["quantity"]),
                int(line["price_minor"]),
                line["currency"],
            )
        )
    if not 1 <= len(lines) <= 20 or len({l.sku for l in lines}) != len(lines):
        raise ValueError("Invalid persisted lines.")
    for key in ("created_at", "expires_at"):
        if not isinstance(row[key], datetime) or row[key].tzinfo is None:
            raise ValueError("Invalid persisted timestamp.")
    if row["expires_at"] <= row["created_at"]:
        raise ValueError("Invalid preparation lifetime.")
    for key in ("reserved_at", "cancelled_at"):
        if row[key] is not None and (
            not isinstance(row[key], datetime)
            or row[key].tzinfo is None
            or row[key] < row["created_at"]
        ):
            raise ValueError("Invalid lifecycle timestamp.")
    status = TenantOrderStatus(row["status"])
    if (status is TenantOrderStatus.RESERVED and row["reserved_at"] is None) or (
        status is TenantOrderStatus.CANCELLED and row["cancelled_at"] is None
    ):
        raise ValueError("Invalid order lifecycle.")
    if (
        status is TenantOrderStatus.PREPARED
        and (row["reserved_at"] is not None or row["cancelled_at"] is not None)
    ) or (status is TenantOrderStatus.RESERVED and row["cancelled_at"] is not None):
        raise ValueError("Contradictory order lifecycle.")
    if (
        row["reserved_at"] is not None
        and row["cancelled_at"] is not None
        and row["cancelled_at"] < row["reserved_at"]
    ):
        raise ValueError("Invalid cancellation timestamp.")
    return TenantOrder(
        org,
        row["order_id"],
        row["customer_id"],
        row["created_by"],
        status,
        tuple(lines),
        row["operation_id"],
        row["preparation_digest"],
        row["created_at"],
        row["expires_at"],
        row["reserved_at"],
        row["cancelled_at"],
        int(row["revision"]),
    )


class MongoBuyerAssociations:
    """Read-only implementation of the A port; provisioning remains explicit."""

    def __init__(self, database):
        self.db = _database(database)

    def find(self, organization_id, user_id, customer_id):
        try:
            MigrationRunner(self.db).require_version(2)
            query = {
                k: identifier(v)
                for k, v in {
                    "organization_id": organization_id,
                    "user_id": user_id,
                    "customer_id": customer_id,
                }.items()
            }
            rows = list(self.db.b2b_buyers.find(query).limit(2))
            if len(rows) > 1:
                raise ValueError("Ambiguous buyer association.")
            return tuple(
                BuyerAssociation(
                    r["association_id"],
                    r["organization_id"],
                    r["user_id"],
                    r["customer_id"],
                    BuyerAssociationStatus(r["status"]),
                    str(_integer(r["revision"])),
                )
                for r in rows
            )
        except (PyMongoError, MigrationError, KeyError, ValueError, TypeError):
            raise BuyerAssociationUnavailable(
                "Buyer association unavailable."
            ) from None


class MongoTenantOrders:
    """Implements TenantOrdersPort. Trusted composition MUST inject authorization.

    Identity records and buyer grants live on this same DB/replica set. Touching
    their fence counters serializes sensitive operations with status/role changes.
    Authentication/session validity is freshly resolved too, but provider/logout
    revocation is not distributed atomically with this Mongo transaction.
    """

    def __init__(self, database, authorization, *, clock=None):
        self.db = _database(database).with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=UTC)
        )
        self.authorization = authorization
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self):
        now = self.clock().astimezone(UTC)
        return now.replace(microsecond=(now.microsecond // 1000) * 1000)

    def _current(self, request, context):
        if self.authorization is None:
            raise TenantOrdersUnavailable("Tenant authorization unavailable.")
        if not isinstance(context, TenantContext):
            raise AuthorizationDenied("Current tenant context required.")
        try:
            current = self.authorization.resolve_context(request)
        except (TenantContextUnavailable, IdentityRepositoryError):
            raise TenantOrdersUnavailable("Tenant identity unavailable.") from None
        if current != context:
            raise AuthorizationDenied("Authorization context changed.")
        return context.principal

    def _fence(self, collection, query, session):
        rows = list(collection.find(query, session=session).limit(2))
        if len(rows) != 1:
            raise AuthorizationDenied("Active unambiguous authorization required.")
        if (
            collection.update_one(
                {**query, "_id": rows[0]["_id"]},
                {"$inc": {"authorization_fence": 1}},
                session=session,
            ).matched_count
            != 1
        ):
            raise AuthorizationDenied("Authorization changed.")

    def _identity(self, principal, session):
        # Writes on the same documents as revocation force a write conflict/retry.
        self._fence(
            self.db.b2b_users,
            {
                "user_id": principal.user_id,
                "status": "active",
                "external_identity.issuer": principal.issuer,
                "external_identity.subject": principal.subject,
            },
            session,
        )
        self._fence(
            self.db.b2b_organizations,
            {
                "organization_id": principal.organization_id,
                "status": "active",
            },
            session,
        )
        self._fence(
            self.db.b2b_memberships,
            {
                "membership_id": principal.membership_id,
                "user_id": principal.user_id,
                "organization_id": principal.organization_id,
                "status": "active",
                "role": principal.role.value,
            },
            session,
        )

    def _buyer(self, org, user, customer, session):
        query = {"organization_id": org, "user_id": user, "customer_id": customer}
        rows = list(self.db.b2b_buyers.find(query, session=session).limit(2))
        if len(rows) > 1:
            raise TenantOrdersUnavailable("Ambiguous buyer association.")
        if not rows or rows[0]["status"] != "active":
            raise TenantOrderNotFound("Order or buyer unavailable.")
        row = rows[0]
        if _integer(row["revision"]) < 1:
            raise ValueError("Invalid buyer revision.")
        result = self.db.b2b_buyers.update_one(
            {
                **query,
                "_id": row["_id"],
                "status": "active",
                "revision": row["revision"],
            },
            {"$inc": {"authorization_fence": 1}},
            session=session,
        )
        if result.matched_count != 1:
            raise AuthorizationDenied("Buyer association changed.")

    def _load(self, org, order_id, session):
        row = self.db.b2b_orders.find_one(
            {"organization_id": org, "order_id": identifier(order_id)}, session=session
        )
        if row is None:
            raise TenantOrderNotFound("Order or buyer unavailable.")
        _decode(row, org)
        return row

    @staticmethod
    def _permission(principal, action, row=None):
        if action in ("prepare", "confirm"):
            if P.ORDER_CREATE not in principal.permissions or (
                row is not None and row["created_by"] != principal.user_id
            ):
                raise AuthorizationDenied("Order creation denied.")
        else:
            own, organization = (
                (P.ORDER_READ_OWN, P.ORDER_READ_ORGANIZATION)
                if action == "get"
                else (P.ORDER_CANCEL_OWN, P.ORDER_CANCEL_ORGANIZATION)
            )
            if organization not in principal.permissions and not (
                own in principal.permissions and row["created_by"] == principal.user_id
            ):
                # Missing and foreign/other-owner orders have identical outcomes.
                raise TenantOrderNotFound("Order or buyer unavailable.")

    def _transaction(self, request, context, body, reconcile=None):
        try:
            with timeout(15):
                MigrationRunner(self.db).require_version(2)
                for attempt in range(4):
                    principal = self._current(request, context)
                    with self.db.client.start_session() as session:
                        session.start_transaction(
                            read_concern=ReadConcern("snapshot"),
                            write_concern=WriteConcern("majority", wtimeout=5000),
                            read_preference=ReadPreference.PRIMARY,
                            max_commit_time_ms=3000,
                        )
                        committing = False
                        try:
                            self._identity(principal, session)
                            result = body(principal, session)
                            # Recheck session + identity outside snapshot before commit.
                            self._current(request, context)
                            committing = True
                            for commit_attempt in range(4):
                                try:
                                    session.commit_transaction()
                                    return result
                                except PyMongoError as error:
                                    if error.has_error_label(
                                        "UnknownTransactionCommitResult"
                                    ):
                                        if commit_attempt < 3:
                                            continue  # Retry commit only, never body.
                                        raise TenantOrderOutcomeUnknown(
                                            "Order outcome unknown; retry the same operation."
                                        ) from None
                                    raise
                        except PyMongoError as error:
                            if not committing and session.in_transaction:
                                session.abort_transaction()
                            if error.has_error_label("UnknownTransactionCommitResult"):
                                raise TenantOrderOutcomeUnknown(
                                    "Order outcome unknown; retry the same operation."
                                ) from None
                            if attempt < 3 and (
                                isinstance(error, DuplicateKeyError)
                                or error.has_error_label("TransientTransactionError")
                            ):
                                continue
                            if committing or isinstance(error, ConnectionFailure):
                                raise TenantOrderOutcomeUnknown(
                                    "Order outcome unknown; retry the same operation."
                                ) from None
                            raise
                raise TenantOrdersUnavailable("Bounded order retries exhausted.")
        except TenantOrderOutcomeUnknown:
            # A majority read of the ledger may establish commit; absence never
            # establishes abort. No new body/operation is issued here.
            if reconcile is not None:
                try:
                    self._current(request, context)
                    result = reconcile()
                    if result is not None:
                        return result
                except (PyMongoError, TenantOrdersUnavailable):
                    pass
            raise
        except (PyMongoError, MigrationError, TenantContextUnavailable):
            raise TenantOrdersUnavailable(
                "B2B order dependencies unavailable."
            ) from None
        except (KeyError, ValueError, TypeError):
            raise TenantOrdersUnavailable(
                "Invalid persisted B2B order facts."
            ) from None

    def _command(self, request, context, command, action, change):
        expected = {
            "prepare": PrepareTenantOrder,
            "confirm": ConfirmTenantOrder,
            "cancel": CancelTenantOrder,
        }[action]
        if not isinstance(command, expected):
            raise TypeError("Typed order command required.")
        principal = self._current(request, context)
        org = principal.organization_id
        payload = asdict(command)
        if action == "prepare":
            payload["lines"] = sorted(payload["lines"], key=lambda l: l["sku"])
        digest = fingerprint(
            {"organization": org, "actor": principal.user_id, "command": payload}
        )
        key = {
            "organization_id": org,
            "namespace": action,
            "operation_id": command.operation_id,
        }

        def replay(principal, session):
            operation = self.db.b2b_order_operations.find_one(key, session=session)
            if operation is None:
                return None
            if (
                operation["fingerprint"] != digest
                or operation["user_id"] != principal.user_id
            ):
                raise TenantOrderConflict("Operation key has different content.")
            row = self._load(org, operation["order_id"], session)
            self._permission(principal, action, row if action != "prepare" else None)
            self._buyer(org, row["created_by"], row["customer_id"], session)
            return _decode(row, org)

        def body(principal, session):
            self._permission(principal, action) if action in (
                "prepare",
                "confirm",
            ) else None
            existing = replay(principal, session)
            if existing is not None:
                return existing
            row = change(principal, session)
            self.db.b2b_order_operations.insert_one(
                {
                    **key,
                    "user_id": principal.user_id,
                    "fingerprint": digest,
                    "order_id": row["order_id"],
                    "created_at": self._now(),
                },
                session=session,
            )
            return _decode(row, org)

        # Reconciliation itself uses fresh authorization and a transaction, not
        # an unguarded ledger read that leaks results after a revocation.
        def reconcile():
            return self._transaction(request, context, replay)

        return self._transaction(request, context, body, reconcile)

    def prepare(self, request, context, command):
        if not isinstance(command, PrepareTenantOrder):
            raise TypeError("Typed preparation required.")

        def change(principal, session):
            org = principal.organization_id
            self._buyer(org, principal.user_id, command.customer_id, session)
            lines = []
            for wanted in sorted(command.lines, key=lambda l: l.sku):
                product = self.db.b2b_products.find_one(
                    {"organization_id": org, "sku": wanted.sku}, session=session
                )
                if product is None:
                    raise TenantOrderNotFound("Order or product unavailable.")
                facts = _product(product, org)
                stock = _stock(product, org)
                if facts.status != "active" or stock.available < wanted.quantity:
                    raise TenantOrderConflict("Insufficient active stock.")
                lines.append(
                    {
                        "product_id": product["_id"],
                        "sku": wanted.sku,
                        "quantity": wanted.quantity,
                        "price_minor": facts.price_minor,
                        "currency": facts.currency,
                    }
                )
            now = self._now()
            row = {
                "organization_id": org,
                "order_id": uuid4().hex,
                "customer_id": command.customer_id,
                "created_by": principal.user_id,
                "status": "prepared",
                "lines": lines,
                "operation_id": command.operation_id,
                "session_binding": fingerprint(context.authorization_context_id),
                "created_at": now,
                "expires_at": now + timedelta(minutes=30),
                "reserved_at": None,
                "cancelled_at": None,
                "revision": 1,
            }
            row["preparation_digest"] = _proof(row)
            self.db.b2b_orders.insert_one(row, session=session)
            return row

        return self._command(request, context, command, "prepare", change)

    def confirm(self, request, context, command):
        if not isinstance(command, ConfirmTenantOrder):
            raise TypeError("Typed explicit confirmation required.")

        def change(principal, session):
            org = principal.organization_id
            row = self._load(org, command.order_id, session)
            self._permission(principal, "confirm", row)
            self._buyer(org, row["created_by"], row["customer_id"], session)
            if (
                row["status"] != "prepared"
                or row["expires_at"] <= self._now()
                or row["preparation_digest"] != command.preparation_digest
                or row["session_binding"]
                != fingerprint(context.authorization_context_id)
            ):
                raise TenantOrderConflict("Preparation changed, expired or consumed.")
            for line in row["lines"]:
                product = self.db.b2b_products.find_one(
                    {
                        "organization_id": org,
                        "_id": line["product_id"],
                        "sku": line["sku"],
                    },
                    session=session,
                )
                if product is None:
                    raise TenantOrderConflict("Prepared product unavailable.")
                facts = _product(product, org)
                if (
                    facts.price_minor != line["price_minor"]
                    or facts.currency != line["currency"]
                ):
                    raise TenantOrderConflict("Prepared price changed.")
                result = self.db.b2b_products.update_one(
                    {
                        "organization_id": org,
                        "_id": line["product_id"],
                        "sku": line["sku"],
                        "status": "active",
                        "revision": facts.revision,
                        "price_minor": line["price_minor"],
                        "currency": line["currency"],
                        "$expr": {
                            "$gte": [
                                {"$subtract": ["$quantity", "$reserved"]},
                                line["quantity"],
                            ]
                        },
                    },
                    {"$inc": {"reserved": line["quantity"], "revision": 1}},
                    session=session,
                )
                if result.matched_count != 1:
                    raise TenantOrderConflict("Insufficient active stock.")
            now = self._now()
            result = self.db.b2b_orders.update_one(
                {
                    "organization_id": org,
                    "order_id": row["order_id"],
                    "created_by": principal.user_id,
                    "status": "prepared",
                    "revision": row["revision"],
                },
                {
                    "$set": {"status": "reserved", "reserved_at": now},
                    "$inc": {"revision": 1},
                },
                session=session,
            )
            if result.matched_count != 1:
                raise TenantOrderConflict("Preparation changed.")
            return {
                **row,
                "status": "reserved",
                "reserved_at": now,
                "revision": row["revision"] + 1,
            }

        return self._command(request, context, command, "confirm", change)

    def cancel(self, request, context, command):
        if not isinstance(command, CancelTenantOrder):
            raise TypeError("Typed cancellation required.")

        def change(principal, session):
            org = principal.organization_id
            row = self._load(org, command.order_id, session)
            self._permission(principal, "cancel", row)
            self._buyer(org, row["created_by"], row["customer_id"], session)
            if row["status"] == "cancelled":
                return row
            if row["status"] not in ("prepared", "reserved"):
                raise TenantOrderConflict("Order cannot be cancelled.")
            if row["status"] == "reserved":
                for line in row["lines"]:
                    product = self.db.b2b_products.find_one(
                        {
                            "organization_id": org,
                            "_id": line["product_id"],
                            "sku": line["sku"],
                        },
                        session=session,
                    )
                    if product is None:
                        raise TenantOrdersUnavailable("Reserved stock unavailable.")
                    facts = _stock(product, org)
                    result = self.db.b2b_products.update_one(
                        {
                            "organization_id": org,
                            "_id": line["product_id"],
                            "sku": line["sku"],
                            "revision": facts.revision,
                            "reserved": {"$gte": line["quantity"]},
                        },
                        {"$inc": {"reserved": -line["quantity"], "revision": 1}},
                        session=session,
                    )
                    if result.matched_count != 1:
                        raise TenantOrdersUnavailable("Reserved stock inconsistent.")
            now = self._now()
            result = self.db.b2b_orders.update_one(
                {
                    "organization_id": org,
                    "order_id": row["order_id"],
                    "status": row["status"],
                    "revision": row["revision"],
                },
                {
                    "$set": {"status": "cancelled", "cancelled_at": now},
                    "$inc": {"revision": 1},
                },
                session=session,
            )
            if result.matched_count != 1:
                raise TenantOrderConflict("Order changed.")
            return {
                **row,
                "status": "cancelled",
                "cancelled_at": now,
                "revision": row["revision"] + 1,
            }

        return self._command(request, context, command, "cancel", change)

    def get(self, request, context, order_id):
        identifier(order_id)

        def read(principal, session):
            row = self._load(principal.organization_id, order_id, session)
            self._permission(principal, "get", row)
            self._buyer(
                principal.organization_id,
                row["created_by"],
                row["customer_id"],
                session,
            )
            return _decode(row, principal.organization_id)

        return self._transaction(request, context, read)
