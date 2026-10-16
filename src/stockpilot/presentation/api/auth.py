from secrets import compare_digest
import math

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from stockpilot.application.customer_identity import validate_customer_id
from stockpilot.infrastructure.config.settings import Settings


_backoffice_bearer = HTTPBearer(auto_error=False, scheme_name="BackofficeBearer")
_customer_bearer = HTTPBearer(auto_error=False, scheme_name="CustomerBearer")


def require_customer_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_customer_bearer),
) -> str:
    settings: Settings = request.app.state.settings
    configured = settings.customer_jwt_secret
    secret = configured.get_secret_value() if configured is not None else None
    if (secret is None or len(secret.encode("utf-8")) < 32
            or bool(settings.customer_jwt_issuer) != bool(settings.customer_jwt_audience)):
        raise HTTPException(
            status_code=503,
            detail="Customer authentication is not configured.",
        )

    authorization = request.headers.get("authorization", "")
    scheme, separator, raw_token = authorization.partition(" ")
    if (
        len(request.headers.getlist("authorization")) != 1
        or credentials is None
        or not separator
        or not raw_token
        or any(character.isspace() for character in raw_token)
        or credentials.scheme.casefold() != "bearer"
        or scheme.casefold() != "bearer"
        or raw_token != credentials.credentials
    ):
        raise _invalid_customer_credentials()

    try:
        claims = jwt.decode(
            raw_token,
            secret,
            algorithms=["HS256"],
            issuer=settings.customer_jwt_issuer,
            audience=settings.customer_jwt_audience,
            options={"require": ["exp", "customer_id"]},
        )
    except (jwt.InvalidTokenError, OverflowError, TypeError, ValueError):
        raise _invalid_customer_credentials() from None

    customer_id = claims.get("customer_id")
    expiration = claims.get("exp")
    if (
        isinstance(expiration, bool)
        or not isinstance(expiration, (int, float))
        or (isinstance(expiration, float) and not math.isfinite(expiration))
        or not isinstance(customer_id, str)
    ):
        raise _invalid_customer_credentials()
    try:
        validate_customer_id(customer_id)
    except ValueError:
        raise _invalid_customer_credentials() from None
    return customer_id.strip()


def _invalid_customer_credentials() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid customer credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_backoffice_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_backoffice_bearer),
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
