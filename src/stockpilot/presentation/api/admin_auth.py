"""Local operator authentication with session-bound CSRF protection."""

from collections import deque
from functools import lru_cache
import hashlib
import json
import re
import secrets
from threading import RLock
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.application.ports.admin_session_store import (
    AdminSessionStore, LoginAttemptsExceeded, Session, SessionCapacityReached,
    SessionStoreUnavailable,
)
from stockpilot.presentation.api.auth import _backoffice_bearer, require_backoffice_token


ITERATIONS = 600_000
LOGIN_WINDOW = 60
MAX_ATTEMPT_KEYS = 1024
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_admin_cookie = APIKeyCookie(name="__Host-stockpilot_admin", auto_error=False, scheme_name="AdminSessionCookie")


def password_verifier(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


class AdminSessions:
    backend = "memory"

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.attempts: dict[str, deque[float]] = {}
        self.global_attempts: deque[float] = deque()
        self.lock = RLock()
        self.clock = time.monotonic
        self.configuration = None

    def prune(self, now: float) -> None:
        for key, session in list(self.sessions.items()):
            if session.expires_at <= now:
                del self.sessions[key]
        for key, attempts in list(self.attempts.items()):
            while attempts and attempts[0] <= now - LOGIN_WINDOW:
                attempts.popleft()
            if not attempts:
                del self.attempts[key]
        while self.global_attempts and self.global_attempts[0] <= now - LOGIN_WINDOW:
            self.global_attempts.popleft()

    def attempt(self, address: str) -> None:
        with self.lock:
            now = self.clock()
            self.prune(now)
            attempts = self.attempts.get(address)
            if (len(self.global_attempts) >= 50
                    or (attempts is not None and len(attempts) >= 5)
                    or (attempts is None and len(self.attempts) >= MAX_ATTEMPT_KEYS)):
                raise HTTPException(429, "Too many login attempts.", headers={"Retry-After": "60"})
            self.attempts.setdefault(address, deque()).append(now)
            self.global_attempts.append(now)

    def configure(self, fingerprint: str) -> None:
        with self.lock:
            if self.configuration != fingerprint:
                self.sessions.clear()
                self.configuration = fingerprint

    def get(self, token: str, fingerprint: str) -> Session | None:
        with self.lock:
            self.prune(self.clock())
            return self.sessions.get(token)

    def create(self, username: str, previous: str, ttl: int, capacity: int,
               fingerprint: str) -> tuple[str, Session]:
        with self.lock:
            self.prune(self.clock())
            if len(self.sessions) >= capacity and previous not in self.sessions:
                raise SessionCapacityReached()
            self.sessions.pop(previous, None)
            token = secrets.token_urlsafe(32)
            session = Session(username, secrets.token_urlsafe(32), self.clock() + ttl)
            self.sessions[token] = session
            return token, session

    def revoke(self, token: str, fingerprint: str) -> None:
        with self.lock:
            self.sessions.pop(token, None)


def store(request: Request) -> AdminSessionStore:
    return request.app.state.admin_sessions


@lru_cache(maxsize=16)
def validated_configuration(key: tuple) -> str:
    username, encoded, trusted, allow_http, environment, backoffice, customer, ttl, capacity, backend = key
    try:
        origin = urlsplit(trusted or "")
        local_http = (allow_http and environment == "development"
                      and origin.scheme == "http" and origin.hostname in LOCAL_HOSTS)
        if (not username or len(username) > 128
                or not re.fullmatch(r"pbkdf2_sha256\$600000\$[0-9a-f]{32,128}\$[0-9a-f]{64}", encoded)
                or len(encoded.split("$")[2]) % 2
                or not origin.netloc or origin.username or origin.password
                or origin.path or origin.query or origin.fragment or origin.port == 0
                or (origin.scheme != "https" and not local_http)
                or (allow_http and not local_http)
                or (backend == "memory" and environment not in {"development", "test"})):
            raise ValueError
        for secret in (backoffice, customer):
            if secret and secrets.compare_digest(
                password_verifier(secret, bytes.fromhex(encoded.split("$")[2])), encoded,
            ):
                raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(503, "Admin authentication is not configured safely.") from None
    return hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()


def configuration_fingerprint(settings: Settings) -> str:
    def secret_value(secret):
        return secret.get_secret_value() if secret else ""

    return validated_configuration((
        settings.admin_username, secret_value(settings.admin_password_verifier),
        settings.admin_trusted_origin, settings.admin_allow_local_http,
        settings.deployment_environment, secret_value(settings.backoffice_api_token),
        secret_value(settings.customer_jwt_secret), settings.admin_session_ttl_seconds,
        settings.admin_max_sessions, settings.admin_session_store,
    ))


def store_failure(operation):
    try:
        return operation()
    except SessionStoreUnavailable:
        raise HTTPException(503, "Admin session storage unavailable.") from None
    except SessionCapacityReached:
        raise HTTPException(503, "Admin session capacity reached.") from None
    except LoginAttemptsExceeded:
        raise HTTPException(429, "Too many login attempts.", headers={"Retry-After": "60"}) from None


def configuration(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    if getattr(store(request), "backend", None) != settings.admin_session_store:
        raise HTTPException(503, "Admin session storage is not configured safely.")
    fingerprint = configuration_fingerprint(settings)
    store_failure(lambda: store(request).configure(fingerprint))
    if request.url.scheme != "https":
        if not (settings.admin_allow_local_http and request.url.hostname in LOCAL_HOSTS):
            raise HTTPException(503, "Admin authentication requires HTTPS.")
    return settings


def cookie_name(settings: Settings) -> str:
    return "stockpilot_admin_local" if settings.admin_allow_local_http else "__Host-stockpilot_admin"


def require_admin_session(request: Request, _cookie: str | None = Depends(_admin_cookie)) -> Session:
    settings = configuration(request)
    session = store_failure(lambda: store(request).get(
        request.cookies.get(cookie_name(settings), ""), configuration_fingerprint(settings),
    ))
    if session is None:
        raise HTTPException(401, "Admin session absent or expired.")
    return session


def require_admin_cookie_only(
    request: Request, session: Session = Depends(require_admin_session),
) -> Session:
    if "authorization" in request.headers:
        raise HTTPException(401, "Admin access requires cookie authentication only.")
    return session


def require_backoffice_read(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_backoffice_bearer),
    _cookie: str | None = Depends(_admin_cookie),
) -> None:
    # An explicit Authorization header must pass the original technical controls.
    if "authorization" in request.headers or not any(
        name in request.cookies for name in ("stockpilot_admin_local", "__Host-stockpilot_admin")
    ):
        require_backoffice_token(request, credentials)
    else:
        require_admin_session(request)


def trusted_origin(request: Request, settings: Settings) -> None:
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] != settings.admin_trusted_origin:
        raise HTTPException(403, "Untrusted admin origin.")


def require_admin_mutation(
    request: Request, session: Session = Depends(require_admin_session),
) -> Session:
    if "authorization" in request.headers:
        raise HTTPException(401, "Admin mutations require cookie authentication only.")
    trusted_origin(request, configuration(request))
    tokens = request.headers.getlist("x-admin-csrf")
    if len(tokens) != 1 or not secrets.compare_digest(tokens[0].encode(), session.csrf_token.encode()):
        raise HTTPException(403, "Invalid admin CSRF token.")
    return session


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=16, max_length=256, repr=False)


class SessionResponse(BaseModel):
    username: str
    csrf_token: str


class SafeSessionRoute(APIRoute):
    validation_message = "Invalid session request."

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default error includes invalid submitted input.
                return JSONResponse(status_code=422, content={"detail": self.validation_message})
        return safe_handler


router = APIRouter(prefix="/backoffice/session", tags=["admin session"], route_class=SafeSessionRoute)


@router.post("/login", response_model=SessionResponse)
def login(body: LoginBody, request: Request, response: Response) -> SessionResponse:
    settings = configuration(request)
    trusted_origin(request, settings)
    # Before a session exists, a custom non-simple header plus exact Origin
    # protects login CSRF. Logout uses the random token bound to that session.
    if (request.headers.getlist("x-admin-csrf") != ["login"]
            or request.headers.get("content-type", "").split(";")[0].strip() != "application/json"):
        raise HTTPException(403, "Invalid login CSRF protection.")
    state = store(request)
    store_failure(lambda: state.attempt(request.client.host if request.client else "unknown"))
    encoded = settings.admin_password_verifier.get_secret_value()
    actual = password_verifier(body.password, bytes.fromhex(encoded.split("$")[2]))
    username_valid = secrets.compare_digest(body.username.encode(), settings.admin_username.encode())
    if not secrets.compare_digest(actual, encoded) or not username_valid:
        raise HTTPException(401, "Invalid admin credentials.")
    token, session = store_failure(lambda: state.create(
        settings.admin_username, request.cookies.get(cookie_name(settings), ""),
        settings.admin_session_ttl_seconds, settings.admin_max_sessions,
        configuration_fingerprint(settings),
    ))
    response.set_cookie(cookie_name(settings), token, httponly=True,
                        secure=not settings.admin_allow_local_http, samesite="strict",
                        path="/", max_age=settings.admin_session_ttl_seconds)
    return SessionResponse(username=session.username, csrf_token=session.csrf_token)


@router.get("/me", response_model=SessionResponse)
def me(session: Session = Depends(require_admin_session)) -> SessionResponse:
    return SessionResponse(username=session.username, csrf_token=session.csrf_token)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response,
           session: Session = Depends(require_admin_mutation)) -> None:
    settings = configuration(request)
    store_failure(lambda: store(request).revoke(
        request.cookies.get(cookie_name(settings), ""), configuration_fingerprint(settings),
    ))
    response.delete_cookie(cookie_name(settings), path="/", httponly=True,
                           secure=not settings.admin_allow_local_http, samesite="strict")
