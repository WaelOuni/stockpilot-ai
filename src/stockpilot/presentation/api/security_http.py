"""HTTP transport policy; identity and business authorization remain separate."""

from collections.abc import Callable

from fastapi import FastAPI
from starlette.datastructures import Headers, URL
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from stockpilot.infrastructure.config.settings import Settings


PRODUCTION_ENVIRONMENTS = frozenset({"production", "production_saas"})
PROBE_HOSTS = ("localhost", "127.0.0.1", "::1")
RESPONSE_HEADERS = {
    b"cache-control": b"no-store",
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"no-referrer",
}
CORS_HEADERS = (
    "Authorization", "Content-Type", "X-Admin-CSRF", "X-Import-Operation-Id",
    "X-Import-Preview", "X-Import-Confirm",
)


class HttpSecurityBoundary:
    def __init__(self, app: ASGIApp, settings_provider: Callable[[], Settings]):
        self.app = app
        self.settings_provider = settings_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def protected_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() not in RESPONSE_HEADERS
                ] + list(RESPONSE_HEADERS.items())
            await send(message)

        settings = self.settings_provider()
        production = settings.deployment_environment in PRODUCTION_ENVIRONMENTS
        headers = Headers(scope=scope)
        health = scope["path"] == "/health"

        async def guarded_app(inner_scope, inner_receive, inner_send):
            if production and not health:
                if (not settings.http_trusted_hosts or any(
                    not origin.startswith("https://") for origin in settings.http_allowed_origins
                )):
                    await JSONResponse(
                        {"detail": "HTTP security is not configured."}, status_code=503,
                    )(inner_scope, inner_receive, inner_send)
                    return
                # Only a trusted ASGI server/proxy may establish the scheme.
                # Raw forwarding headers from a caller never establish HTTPS.
                if inner_scope.get("scheme") != "https":
                    await JSONResponse(
                        {"detail": "HTTPS is required."}, status_code=400,
                    )(inner_scope, inner_receive, inner_send)
                    return
                origins = headers.getlist("origin")
                url = URL(scope=inner_scope)
                hostname = url.hostname or ""
                if ":" in hostname:
                    hostname = f"[{hostname}]"
                default_port = 443 if url.scheme == "https" else 80
                port = f":{url.port}" if url.port not in {None, default_port} else ""
                same_origin = f"{url.scheme}://{hostname}{port}"
                if origins and (len(origins) != 1 or (
                    origins[0] != same_origin and origins[0] not in settings.http_allowed_origins
                )):
                    await JSONResponse(
                        {"detail": "Origin is not permitted."}, status_code=403,
                    )(inner_scope, inner_receive, inner_send)
                    return

            application = self.app
            if settings.http_allowed_origins:
                application = CORSMiddleware(
                    application, allow_origins=settings.http_allowed_origins,
                    allow_credentials=True, allow_methods=["GET", "POST", "PATCH"],
                    allow_headers=CORS_HEADERS, expose_headers=["X-Request-Id"],
                )
            await application(inner_scope, inner_receive, inner_send)

        if production or settings.http_trusted_hosts:
            # Duplicate Host fields have no unambiguous security meaning.
            if len(headers.getlist("host")) != 1:
                await JSONResponse({"detail": "Invalid host header."}, status_code=400)(
                    scope, receive, protected_send,
                )
                return
            await TrustedHostMiddleware(
                guarded_app, allowed_hosts=settings.http_trusted_hosts or PROBE_HOSTS,
                www_redirect=False,
            )(scope, receive, protected_send)
        else:
            await guarded_app(scope, receive, protected_send)


def install_http_security(app: FastAPI) -> None:
    app.add_middleware(HttpSecurityBoundary, settings_provider=lambda: app.state.settings)
