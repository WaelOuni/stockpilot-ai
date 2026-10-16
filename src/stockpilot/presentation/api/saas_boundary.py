"""Close SaaS access until the identity and tenant contracts are implemented."""
from starlette.responses import JSONResponse


class SaasBoundaryMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = scope["app"].state.settings
        environment = settings.deployment_environment
        # There is deliberately no flag which enables shared repositories in SaaS.
        # Health exposes liveness only, including while sensitive access is closed.
        if environment not in {"development", "test", "local_demo"} and scope["path"] != "/health":
            response = JSONResponse(
                {"detail": "SaaS access is unavailable pending identity and tenant isolation."},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)
