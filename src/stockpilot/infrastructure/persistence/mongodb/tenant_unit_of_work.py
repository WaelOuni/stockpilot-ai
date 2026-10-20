"""Shared B2/B3 transaction boundary. No provisioning or I/O at import."""

import hashlib
import json
from datetime import UTC, datetime

from bson.codec_options import CodecOptions
from pymongo import ReadPreference, timeout
from pymongo.errors import ConnectionFailure, DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.ports.tenant_authorization import TenantContextUnavailable
from stockpilot.application.ports.tenant_orders import (
    TenantOrderOutcomeUnknown,
    TenantOrdersUnavailable,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationError,
    MigrationRunner,
    _database,
)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


class MongoTenantUnitOfWork:
    schema_version = 2

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

    def _transaction(self, request, context, body, reconcile=None):
        try:
            with timeout(15):
                MigrationRunner(self.db).require_version(self.schema_version)
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
