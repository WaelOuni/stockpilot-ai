"""Cookie boundary before reading any business body; bounded worker admission."""

import secrets
import threading
import time

import anyio
from fastapi.responses import JSONResponse
from starlette.requests import Request

from stockpilot.application.ports.bff_security import BffUnauthenticated, BffUnavailable
from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.ports.tenant_authorization import TenantContextUnavailable
from stockpilot.application.ports.tenant_catalogue import TenantCatalogueUnavailable
from stockpilot.application.ports.tenant_orders import (
    TenantOrderConflict,
    TenantOrderNotFound,
    TenantOrderOutcomeUnknown,
    TenantOrdersUnavailable,
)
from stockpilot.application.ports.tenant_stock_operations import (
    TenantStockOperationConflict,
    TenantStockResourceNotFound,
)
from stockpilot.application.services.bff_sessions import BffForbidden
from stockpilot.application.services.identity_authorization import AuthorizationDenied
from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.infrastructure.identity.bff_tenant_context import BffTenantRequest
from stockpilot.presentation.api.routes.bff_auth import (
    SESSION_COOKIE,
    cookie,
    origin,
    service,
)
from stockpilot.presentation.api.routes.bff_auth import (
    auth_worker as worker,
)


def failure(error, operation=None):
    status, code = 500, "INTERNAL_ERROR"
    if isinstance(error, BffUnauthenticated):
        status, code = 401, "UNAUTHENTICATED"
    elif isinstance(error, (BffForbidden, AuthorizationDenied)):
        status, code = 403, "FORBIDDEN"
    elif isinstance(error, (TenantOrderNotFound, TenantStockResourceNotFound)):
        status, code = 404, "NOT_FOUND"
    elif isinstance(error, (TenantOrderConflict, TenantStockOperationConflict)):
        status, code = 409, "CONFLICT"
    elif isinstance(error, TimeoutError):
        status, code = 503, "OUTCOME_UNKNOWN" if operation else "DEPENDENCY_UNAVAILABLE"
    elif isinstance(error, TenantOrderOutcomeUnknown):
        status, code = 503, "OUTCOME_UNKNOWN"
    elif isinstance(
        error,
        (
            BffUnavailable,
            IdentityRepositoryError,
            TenantContextUnavailable,
            TenantCatalogueUnavailable,
            TenantOrdersUnavailable,
        ),
    ):
        status, code = 503, "DEPENDENCY_UNAVAILABLE"
    elif isinstance(error, (ValueError, TypeError)):
        status, code = 422, "INVALID_REQUEST"
    body = {"code": code}
    if operation is not None:
        body["operation"] = operation
    response = JSONResponse(body, status_code=status)
    if status == 401:
        response.delete_cookie(
            SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
        )
    return response


class BusinessBoundary:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.lock = threading.Lock()
        self.inflight = self.used = 0
        self.window = time.monotonic()

    def admit(self):
        with self.lock:
            now = time.monotonic()
            if now - self.window >= 60:
                self.window, self.used = now, 0
            if (
                self.inflight >= self.settings.max_inflight
                or self.used >= self.settings.requests_per_minute
            ):
                return False
            self.inflight += 1
            self.used += 1
            return True

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/b2b/"):
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        if not self.admit():
            return await JSONResponse(
                {"code": "BUSY"}, 429, headers={"Retry-After": "1"}
            )(scope, receive, send)
        try:
            if not scope["path"].startswith("/b2b/api/"):
                await self.app(scope, receive, send)
                return
            if getattr(request.app.state, "bff_business", None) is None:
                raise BffUnavailable()
            if any(
                request.headers.getlist(k)
                for k in (
                    "authorization",
                    "x-organization-id",
                    "x-user-id",
                    "x-role",
                    "x-permissions",
                    "x-customer-id",
                )
            ):
                return await JSONResponse({"code": "UNTRUSTED_IDENTITY_HEADER"}, 400)(
                    scope, receive, send
                )
            session, principal = await worker(
                request, service(request).current, cookie(request, SESSION_COOKIE)
            )
            scope.setdefault("state", {})["tenant_request"] = BffTenantRequest(
                cookie(request, SESSION_COOKIE)
            )
            scope["state"]["tenant_context"] = TenantContext(
                principal, session.session_hash
            )
            mutation = scope["method"] not in ("GET", "HEAD", "OPTIONS")
            if mutation:
                origin(request)
            # Vue binds reads to the session it actually restored too. A cookie
            # changed by another tab must not return another identity's data into
            # an old UI. Legacy HTTP consumers without a read proof retain their
            # authenticated read contract; mutations always require the proof.
            csrf = request.headers.getlist("x-bff-csrf")
            if (mutation or csrf) and (
                len(csrf) != 1
                or len(csrf[0]) > 128
                or not secrets.compare_digest(
                    csrf[0].encode(), session.csrf_token.encode()
                )
            ):
                raise BffForbidden()
            limit = (
                256 * 1024 if scope["path"] == "/b2b/api/imports/preview" else 64 * 1024
            )
            lengths = request.headers.getlist("content-length")
            if len(lengths) > 1 or (
                lengths and (not lengths[0].isdigit() or len(lengths[0]) > 10)
            ):
                return await JSONResponse({"code": "INVALID_LENGTH"}, 400)(
                    scope, receive, send
                )
            if lengths and int(lengths[0]) > limit:
                return await JSONResponse({"code": "BODY_TOO_LARGE"}, 413)(
                    scope, receive, send
                )
            if scope["method"] == "POST":
                expected = (
                    "text/csv"
                    if scope["path"] == "/b2b/api/imports/preview"
                    else "application/json"
                )
                if (
                    request.headers.get("content-type", "").split(";")[0].strip()
                    != expected
                ):
                    return await JSONResponse({"code": "INVALID_MEDIA_TYPE"}, 422)(
                        scope, receive, send
                    )
            body = bytearray()
            try:
                with anyio.fail_after(self.settings.body_timeout_seconds):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > limit:
                            return await JSONResponse({"code": "BODY_TOO_LARGE"}, 413)(
                                scope, receive, send
                            )
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await JSONResponse({"code": "BODY_TIMEOUT"}, 408)(
                    scope, receive, send
                )
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            await self.app(scope, replay, send)
        except (
            BffUnauthenticated,
            BffForbidden,
            BffUnavailable,
            IdentityRepositoryError,
        ) as error:
            await failure(error)(scope, receive, send)
        finally:
            with self.lock:
                self.inflight -= 1


def install_business(app, settings, router):
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError

    async def business_error(request, error):
        return failure(error, getattr(request.state, "operation", None))

    async def invalid_request(request, error):
        return JSONResponse({"code": "INVALID_REQUEST"}, status_code=422)

    for kind in (
        AuthorizationDenied,
        TenantContextUnavailable,
        TenantCatalogueUnavailable,
        TenantOrderConflict,
        TenantOrderNotFound,
        TenantOrdersUnavailable,
        TenantStockOperationConflict,
        TenantStockResourceNotFound,
        ValueError,
        TypeError,
        TimeoutError,
    ):
        app.add_exception_handler(kind, business_error)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(ValidationError, invalid_request)
    app.include_router(router)
    app.add_middleware(BusinessBoundary, settings=settings)
