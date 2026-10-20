"""Browser auth endpoints for the separate, closed-business BFF test app."""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from stockpilot.application.ports.bff_security import BffUnauthenticated, BffUnavailable
from stockpilot.application.services.bff_sessions import BffForbidden

SESSION_COOKIE = "__Host-stockpilot_bff"
TRANSACTION_COOKIE = "__Host-stockpilot_oidc"
router = APIRouter(prefix="/b2b/auth", tags=["B2B authentication"])


def cookie(request: Request, name: str) -> str | None:
    values = []
    for header in request.headers.getlist("cookie"):
        for pair in header.split(";"):
            key, separator, value = pair.strip().partition("=")
            if key == name and separator:
                values.append(value)
    if len(values) > 1:
        raise BffUnauthenticated("Ambiguous cookie.")
    return values[0] if values else None


def service(request: Request):
    configured = getattr(request.app.state, "bff_service", None)
    if configured is None:
        raise BffUnavailable("BFF unavailable.")
    return configured


def origin(request: Request) -> None:
    if request.headers.getlist("origin") != [request.app.state.bff_settings.origin]:
        raise BffForbidden("Origin refused.")


async def bounded_json(request: Request) -> dict:
    chunks = []
    size = 0
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                size += len(chunk)
                if size > 4096:
                    raise BffUnauthenticated("Invalid login request.")
                chunks.append(chunk)
        result = json.loads(b"".join(chunks))
        if not isinstance(result, dict) or set(result) != {"organization_id"}:
            raise ValueError
        return result
    except (ValueError, UnicodeError, TimeoutError):
        raise BffUnauthenticated("Invalid login request.") from None


@router.post("/login")
async def login(request: Request):
    origin(request)
    if (
        request.headers.getlist("x-bff-csrf") != ["login"]
        or request.headers.get("content-type", "").split(";")[0].strip()
        != "application/json"
    ):
        raise BffForbidden("Login CSRF refused.")
    body = await bounded_json(request)
    provider_url, browser = await asyncio.to_thread(
        service(request).begin,
        body["organization_id"],
        cookie(request, SESSION_COOKIE),
    )
    response = RedirectResponse(provider_url, status_code=303)
    response.set_cookie(
        TRANSACTION_COOKIE,
        browser,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=request.app.state.bff_settings.transaction_seconds,
    )
    return response


@router.get("/callback")
async def callback(request: Request):
    allowed = {"state", "code", "error", "error_description", "session_state", "iss"}
    query = request.query_params
    if (
        set(query) - allowed
        or any(len(query.getlist(k)) != 1 for k in query)
        or "state" not in query
        or ("code" in query) == ("error" in query)
    ):
        raise BffUnauthenticated("Invalid callback.")
    # RFC 9207 response issuer is checked when supplied; token issuer is always checked.
    if "iss" in query and query["iss"] != request.app.state.bff_settings.issuer:
        raise BffUnauthenticated("Invalid callback issuer.")
    token = await asyncio.to_thread(
        service(request).complete,
        query["state"],
        cookie(request, TRANSACTION_COOKIE),
        query.get("code"),
        "error" in query,
    )
    response = RedirectResponse("/b2b/auth/session", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=request.app.state.bff_settings.session_absolute_seconds,
    )
    response.delete_cookie(
        TRANSACTION_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
    )
    return response


@router.get("/session")
@router.get("/me")
async def session(request: Request):
    stored, principal = await asyncio.to_thread(
        service(request).current,
        cookie(request, SESSION_COOKIE),
    )
    return {
        "user_id": principal.user_id,
        "organization_id": principal.organization_id,
        "role": principal.role.value,
        "permissions": sorted(p.value for p in principal.permissions),
        "csrf_token": stored.csrf_token,
        "expires_at": stored.absolute_expires_at.isoformat(),
    }


@router.post("/logout")
async def logout(request: Request):
    origin(request)
    csrf = request.headers.getlist("x-bff-csrf")
    if len(csrf) != 1 or len(csrf[0]) > 128:
        raise BffForbidden("CSRF refused.")
    target = await asyncio.to_thread(
        service(request).logout,
        cookie(request, SESSION_COOKIE),
        csrf[0],
    )
    response = JSONResponse({"provider_logout_url": target})
    response.delete_cookie(
        SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/"
    )
    return response


@router.get("/logged-out")
async def logged_out():
    return {"status": "signed_out"}
