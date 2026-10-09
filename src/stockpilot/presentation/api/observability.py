import logging
from uuid import uuid4

from starlette.responses import JSONResponse

from stockpilot.observability import Timer, event, log_context
from stockpilot.telemetry import enrich_span
from stockpilot.metrics import record


logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        enrich_span(**{"stockpilot.request_id": request_id})
        timer = Timer()
        response_started = False

        async def send_response(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                route = scope.get("route")
                method = scope["method"]
                record_attributes = {
                    "http.request.method": method if method in {
                        "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"
                    } else "_OTHER",
                    "http.route": getattr(route, "path", "unmatched"),
                    "http.response.status_code": message["status"],
                }
                record("http.requests", **record_attributes)
                record("http.request.duration", timer.duration_ms / 1000, **record_attributes)
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
                event(logger, "http_request_completed",
                      status_code=message["status"], duration_ms=timer.duration_ms)
            await send(message)

        with log_context(request_id=request_id, method=scope["method"], path=scope["path"]):
            event(logger, "http_request_started")
            try:
                await self.app(scope, receive, send_response)
            except Exception as error:
                event(logger, "http_request_failed", level=logging.ERROR,
                      error_type=type(error).__name__, duration_ms=timer.duration_ms)
                if response_started:
                    raise
                response = JSONResponse({"detail": "Internal server error"}, status_code=500)
                await response(scope, receive, send_response)
