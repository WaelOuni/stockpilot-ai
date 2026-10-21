"""Opt-in isolated BFF factory. Never imports the historical stockpilot.main."""

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

import anyio
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


def create_app(
    settings: BffSettings | None = None, *, service=None, business=None, assistant=None
) -> FastAPI:
    settings = settings or BffSettings()
    configured = False
    policy = None
    if settings.mode in {"b2b_auth_test", "b2b_business_test"}:
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
                **(
                    {
                        "socketTimeoutMS": 5000,
                        "waitQueueTimeoutMS": 1000,
                        "maxPoolSize": settings.max_inflight,
                    }
                    if settings.mode == "b2b_business_test"
                    else {}
                ),
            )
            database = mongo[settings.mongo_database]
            store = MongoBffStore(database)
            try:
                if settings.mode == "b2b_auth_test":
                    initialize_b2b_indexes(database)
                    store.initialize()
                # Business mode never creates schema/indexes at startup.
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
                if settings.mode == "b2b_business_test":
                    from stockpilot.infrastructure.identity.bff_business import (
                        BffBusiness,
                    )

                    app.state.bff_business = BffBusiness.compose(
                        database, app.state.bff_service
                    )
                    if settings.assistant_enabled:
                        from stockpilot.infrastructure.identity.bff_assistant import (
                            compose_assistant,
                        )

                        app.state.bff_assistant = compose_assistant(
                            settings, app.state.bff_business
                        )
            except (BffUnavailable, IdentityRepositoryError, PyMongoError) as error:
                logging.getLogger(__name__).warning(
                    "BFF startup dependency unavailable (%s)", type(error).__name__
                )
                # Startup fails closed. Never include connection, token or exception
                # contents in HTTP replies. No in-memory production fallback.
                app.state.bff_service = None
                app.state.bff_business = None
                app.state.bff_assistant = None
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
    app.state.bff_business = (
        business if configured and settings.mode == "b2b_business_test" else None
    )
    app.state.bff_assistant = (
        assistant if configured and settings.assistant_enabled else None
    )
    app.state.business_limiter = anyio.CapacityLimiter(settings.max_inflight)
    if settings.mode == "b2b_business_test":
        from stockpilot.presentation.api.b2b_boundary import install_business
        from stockpilot.presentation.api.routes.b2b_business import (
            router as business_router,
        )

        install_business(app, settings, business_router)
        if settings.assistant_enabled:
            from stockpilot.presentation.api.routes.b2b_assistant import (
                router as assistant_router,
            )

            app.include_router(assistant_router)

    @app.middleware("http")
    async def closed_boundary(request: Request, call_next):
        request.state.request_id = uuid4().hex
        try:
            if request.url.path != "/health":
                if (
                    settings.mode not in {"b2b_auth_test", "b2b_business_test"}
                    or not configured
                ):
                    response = JSONResponse(
                        {"detail": "BFF unavailable."}, status_code=503
                    )
                elif not secure_transport(request, settings):
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
        response.headers["X-Request-ID"] = request.state.request_id
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

    if settings.mode in {"b2b_auth_test", "b2b_business_test"}:
        app.include_router(router)
    return app


def secure_transport(request, settings):
    expected = urlsplit(settings.origin).netloc
    if request.headers.getlist("host") != [expected]:
        return False
    if settings.mode == "b2b_business_test":
        forwarded = [
            k
            for k in request.headers
            if k == "forwarded" or k.startswith("x-forwarded-")
        ]
        if forwarded:
            peer = request.scope.get("client")
            if (
                not peer
                or peer[0] not in settings.trusted_proxy_ips
                or "forwarded" in forwarded
                or set(forwarded)
                - {"x-forwarded-proto", "x-forwarded-host", "x-forwarded-for"}
                or request.headers.getlist("x-forwarded-proto") != ["https"]
                or request.headers.getlist("x-forwarded-host") != [expected]
            ):
                return False
            # X-Forwarded-For is never an authentication or quota identity.
            request.scope["scheme"] = "https"
    return request.scope.get("scheme") == "https"
