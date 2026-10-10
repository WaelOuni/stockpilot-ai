from secrets import compare_digest

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from stockpilot.infrastructure.config.settings import Settings


_bearer = HTTPBearer(auto_error=False)


def require_backoffice_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    settings: Settings = request.app.state.settings
    configured = settings.backoffice_api_token
    expected = configured.get_secret_value() if configured is not None else None
    if expected is None or not expected.strip():
        raise HTTPException(
            status_code=503,
            detail="Back-office authentication is not configured.",
        )
    authorization = request.headers.get("authorization", "")
    _, separator, raw_token = authorization.partition(" ")
    if (
        credentials is None
        or not separator
        or not raw_token
        or any(character.isspace() for character in raw_token)
        or credentials.scheme.casefold() != "bearer"
        or not compare_digest(credentials.credentials.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid back-office credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
