"""Optional trusted bridge; not composed into any HTTP route by this phase."""

from dataclasses import dataclass, field

from stockpilot.application.ports.bff_security import BffUnauthenticated, BffUnavailable
from stockpilot.application.ports.tenant_authorization import TenantContextUnavailable
from stockpilot.application.services.bff_sessions import BffForbidden, BffSessionService
from stockpilot.domain.entities.tenant_authorization import TenantContext


@dataclass(frozen=True)
class BffTenantRequest:
    """Untrusted cookie input; only BffSessionService can authenticate it."""

    session_cookie: str | None = field(repr=False)


@dataclass(frozen=True)
class BffTenantContextSource:
    sessions: BffSessionService

    def current(self, request_context: object) -> TenantContext | None:
        if not isinstance(request_context, BffTenantRequest):
            return None
        if self.sessions is None:
            raise TenantContextUnavailable("Tenant authentication unavailable.")
        try:
            # current() rechecks expiry/revocation and resolves V13.7.1 stores.
            session, principal = self.sessions.current(request_context.session_cookie)
        except (BffUnauthenticated, BffForbidden):
            return None
        except BffUnavailable:
            raise TenantContextUnavailable(
                "Tenant authentication unavailable."
            ) from None
        return TenantContext(principal, session.session_hash)
