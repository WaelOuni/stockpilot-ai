"""Mandatory server context and explicit B2B schema around every operation."""

from contextlib import contextmanager

import psycopg

from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.ports.tenant_authorization import TenantContextUnavailable
from stockpilot.application.ports.tenant_knowledge import (
    TenantDataOutcomeUnknown,
    TenantDataUnavailable,
)
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.infrastructure.persistence.postgres.tenant_migrations import (
    PostgresMigrationError,
    PostgresTenantMigrations,
    schema_name,
    table,
)


class TenantPostgresBoundary:
    def __init__(self, uri, authorization, schema="stockpilot_b2b"):
        self.uri, self.authorization, self.schema = (
            uri,
            authorization,
            schema_name(schema),
        )

    def current(self, request, context, permission):
        if not isinstance(context, TenantContext):
            raise AuthorizationDenied("Current tenant context required.")
        if self.authorization is None:
            raise TenantDataUnavailable("Tenant identity unavailable.")
        try:
            current = self.authorization.resolve_context(request)
        except (IdentityRepositoryError, TenantContextUnavailable):
            raise TenantDataUnavailable("Tenant identity unavailable.") from None
        if current != context or permission not in current.principal.permissions:
            raise AuthorizationDenied("Current permission required.")
        return current.principal

    def t(self, name):
        return table(self.schema, name)

    @contextmanager
    def connection(self, request, context, permission, *, write=False):
        self.current(request, context, permission)
        try:
            with psycopg.connect(self.uri, connect_timeout=5) as c:
                c.execute("SET LOCAL statement_timeout='10s'")
                c.execute("SET LOCAL lock_timeout='3s'")
                from psycopg import sql

                c.execute(
                    sql.SQL("SET LOCAL search_path TO {}, public").format(
                        sql.Identifier(self.schema)
                    )
                )
                PostgresTenantMigrations(self.uri, self.schema).require(c)
                yield c
                self.current(request, context, permission)
        except PostgresMigrationError:
            raise TenantDataUnavailable("B2B schema unavailable.") from None
        except psycopg.Error:
            error = TenantDataOutcomeUnknown if write else TenantDataUnavailable
            raise error(
                "B2B PostgreSQL operation unavailable; reconcile original operation."
            ) from None
