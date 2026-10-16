"""Bound requests before dispatch; local admission is not a distributed quota."""
from threading import Lock

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from stockpilot.application.resource_errors import (
    ModelInputLimitError, ModelTimeoutError, ModelUnavailableError,
)


CONVERSATION_PATHS = frozenset({"/assistant", "/chat", "/classify"})
CSV_PATHS = frozenset({
    "/backoffice/products/import/preview", "/backoffice/products/import/execute",
})


class InputProtectionMiddleware:
    def __init__(self, app):
        self.app = app
        self._lock = Lock()
        self._inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = scope["app"].state.settings
        path = scope["path"].rstrip("/")
        admitted = False
        if path in CONVERSATION_PATHS and scope["method"] == "POST":
            with self._lock:
                if self._inflight < settings.conversation_max_inflight:
                    self._inflight += 1
                    admitted = True
            if not admitted:
                response = JSONResponse(
                    {"detail": "Conversation service busy; retry later."},
                    status_code=429, headers={"Retry-After": "1"},
                )
                return await response(scope, receive, send)
        try:
            # CSV already bounds its actual request stream separately, after
            # admin authorization. Preserve that existing upload contract.
            if path in CSV_PATHS:
                return await self.app(scope, receive, send)
            limit = settings.http_body_max_bytes
            lengths = [value for key, value in scope["headers"] if key.lower() == b"content-length"]
            if len(lengths) > 1 or (lengths and (len(lengths[0]) > 20 or not lengths[0].isdigit())):
                response = JSONResponse({"detail": "Invalid request size."}, status_code=400)
                return await response(scope, receive, send)
            if lengths and int(lengths[0]) > limit:
                response = JSONResponse({"detail": "Request body too large."}, status_code=413)
                return await response(scope, receive, send)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > limit:
                    response = JSONResponse({"detail": "Request body too large."}, status_code=413)
                    return await response(scope, receive, send)
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def replay_body():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, replay_body, send)
        finally:
            if admitted:
                with self._lock:
                    self._inflight -= 1


async def require_bounded_path_identifiers(request: Request):
    limit = request.app.state.settings.api_identifier_max_chars
    if any(isinstance(value, str) and len(value) > limit for value in request.path_params.values()):
        raise HTTPException(422, "Request identifier exceeds configured limit.")


def validate_conversation_input(request: Request, payload):
    settings = request.app.state.settings
    if len(payload.message) > settings.api_message_max_chars:
        raise HTTPException(422, "Message exceeds configured limit.")
    thread_id = getattr(payload, "thread_id", None)
    if thread_id is not None and len(thread_id) > settings.api_thread_max_chars:
        raise HTTPException(422, "Conversation identifier exceeds configured limit.")


async def safe_validation_error(request: Request, error: RequestValidationError):
    # Error inputs, validator text and even arbitrary extra-field names can
    # contain credentials. Preserve only the source of the first validation
    # error, which also preserves the existing query-error response contract.
    errors = error.errors()
    first_location = errors[0].get("loc", ()) if errors else ()
    root = first_location[0] if first_location else None
    location = [root] if root in {"body", "path", "query", "header", "cookie"} else []
    return JSONResponse({"detail": [{
        "loc": location, "msg": "Invalid request.", "type": "request_validation",
    }]}, status_code=422)


async def safe_model_resource_error(request: Request, error: Exception):
    status, detail = {
        ModelInputLimitError: (413, "Model input exceeds configured resource limit."),
        ModelTimeoutError: (504, "Model service timed out."),
        ModelUnavailableError: (503, "Model service unavailable."),
    }[type(error)]
    return JSONResponse({"detail": detail}, status_code=status)
