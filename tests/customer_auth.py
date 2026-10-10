from datetime import datetime, timedelta, timezone

import jwt
from pydantic import SecretStr

from stockpilot.infrastructure.config.settings import Settings


CUSTOMER_JWT_SECRET = "test-only-customer-jwt-signing-secret-with-32-bytes"


def customer_token(customer_id="customer-a", *, secret=CUSTOMER_JWT_SECRET, **claims):
    payload = {
        "customer_id": customer_id,
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        **claims,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def customer_headers(customer_id="customer-a", *, secret=CUSTOMER_JWT_SECRET, **headers):
    return {
        "Authorization": f"Bearer {customer_token(customer_id, secret=secret)}",
        **headers,
    }


def configure_customer_auth(monkeypatch, app):
    current = getattr(app.state, "settings", None)
    if not isinstance(current, Settings):
        current = Settings(_env_file=None)
    configured = current.model_copy(
        update={"customer_jwt_secret": SecretStr(CUSTOMER_JWT_SECRET)},
    )
    if monkeypatch is None:
        app.state.settings = configured
    else:
        monkeypatch.setattr(app.state, "settings", configured, raising=False)
