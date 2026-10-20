"""Opt-in authentication-only ASGI factory. Does not import stockpilot.main."""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from stockpilot.application.ports.bff_security import BffUnauthenticated, BffUnavailable
from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.application.services.bff_sessions import BffForbidden, BffSessionService
from stockpilot.infrastructure.config.bff_settings import BffSettings
from stockpilot.presentation.api.routes.bff_auth import (
    SESSION_COOKIE,
    TRANSACTION_COOKIE,
    router,
)


def create_app(settings: BffSettings | None = None, *, service=None) -> FastAPI:
    settings = settings or BffSettings()
    configured = False
    policy = None
    if settings.mode == "b2b_auth_test":
        try:
            policy = settings.validated_policy()
            configured = True
        except BffUnavailable:
            pass  # Routes fail 503; no fallback or connection to historical stores.

    @asynccontextmanager
    async def lifespan(app):
        mongo = None
        provider = None
        if configured and service is None:
            from stockpilot.infrastructure.identity.oidc_provider import (
                OidcHttpProvider,
            )
            from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
                MongoIdentityRepository,
                MongoMembershipRepository,
                MongoOrganizationRepository,
                initialize_b2b_indexes,
            )
            from stockpilot.infrastructure.persistence.mongodb.bff_store import (
                MongoBffStore,
            )

            mongo = MongoClient(
                settings.mongo_uri.get_secret_value(),
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                tz_aware=True,
            )
            database = mongo[settings.mongo_database]
            store = MongoBffStore(database)
            try:
                initialize_b2b_indexes(database)
                store.initialize()
                provider = OidcHttpProvider(
                    settings.issuer,
                    settings.client_id,
                    settings.client_secret.get_secret_value(),
                    settings.callback_uri,
                    settings.post_logout_uri,
                    allow_loopback_http=settings.allow_loopback_http_issuer,
                )
                app.state.bff_service = BffSessionService(
                    provider,
                    store,
                    MongoIdentityRepository(database),
                    MongoOrganizationRepository(database),
                    MongoMembershipRepository(database),
                    policy,
                )
            except (BffUnavailable, IdentityRepositoryError, PyMongoError):
                # Startup fails closed. Never include connection, token or exception
                # contents in HTTP replies. No in-memory production fallback.
                app.state.bff_service = None
        try:
            yield
        finally:
            if mongo is not None:
                mongo.close()
            if provider is not None and hasattr(provider, "close"):
                provider.close()

    app = FastAPI(
        title="StockPilot B2B auth test",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.bff_settings = settings
    app.state.bff_service = service if configured else None

    @app.middleware("http")
    async def closed_boundary(request: Request, call_next):
        try:
            if request.url.path != "/health":
                if settings.mode != "b2b_auth_test" or not configured:
                    response = JSONResponse(
                        {"detail": "BFF unavailable."}, status_code=503
                    )
                elif request.scope.get("scheme") != "https" or request.headers.getlist(
                    "host"
                ) != [urlsplit(settings.origin).netloc]:
                    response = JSONResponse(
                        {"detail": "Secure origin required."}, status_code=400
                    )
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        finally:
            # Also scrub unexpected error paths before standard ASGI access logging.
            request.scope["query_string"] = b""
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            }
        )
        return response

    async def unauthenticated(request, error):
        response = JSONResponse(
            {"detail": "Authentication absent, invalid or expired."}, status_code=401
        )
        for name in (SESSION_COOKIE, TRANSACTION_COOKIE):
            response.delete_cookie(
                name, path="/", secure=True, httponly=True, samesite="lax"
            )
        return response

    async def forbidden(request, error):
        return JSONResponse({"detail": "Application access refused."}, status_code=403)

    async def unavailable(request, error):
        return JSONResponse(
            {"detail": "Authentication service unavailable."}, status_code=503
        )

    app.add_exception_handler(BffUnauthenticated, unauthenticated)
    app.add_exception_handler(BffForbidden, forbidden)
    app.add_exception_handler(BffUnavailable, unavailable)

    @app.get("/health")
    def health():
        return {"status": "UP"}

    if settings.mode == "b2b_auth_test":
        app.include_router(router)
    return app
