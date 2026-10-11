#!/usr/bin/env python3
"""Create a short-lived HS256 customer token for local/demo use."""

import argparse
import os
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

import jwt

from stockpilot.application.customer_identity import validate_customer_id
from stockpilot.infrastructure.config.settings import Settings


def _positive_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive number of seconds") from error
    if not 0 < seconds <= 86400:
        raise argparse.ArgumentTypeError("must be between 1 and 86400 seconds")
    return seconds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("customer_id", help="Customer identity to place in the signed token")
    parser.add_argument(
        "--expires-in",
        type=_positive_seconds,
        default=3600,
        metavar="SECONDS",
        help="Token lifetime in seconds, from 1 to 86400 (default: 3600)",
    )
    parser.add_argument("--dev-auto-login", action="store_true", help="Require isolated local DEV configuration")
    args = parser.parse_args()
    try:
        validate_customer_id(args.customer_id)
    except ValueError:
        parser.error("customer_id must be a nonblank string")

    settings = Settings()
    if args.dev_auto_login:
        if (settings.deployment_environment != "development"
                or settings.mongo_database != "stockpilot"
                or urlsplit(settings.mongo_uri).hostname not in {"localhost", "127.0.0.1", "::1"}
                or os.environ.get("STOCKPILOT_DEMO_ENV_DIR")
                or os.environ.get("E2E_CUSTOMER_JWT_SECRET")
                or args.customer_id != "local-client-a"
                or args.expires_in > 3600):
            parser.error("Auto-login requires local DEV, stockpilot, local-client-a and TTL <= 3600")
    configured = settings.customer_jwt_secret
    secret = configured.get_secret_value() if configured is not None else None
    if secret is None or len(secret.encode("utf-8")) < 32:
        parser.error("CUSTOMER_JWT_SECRET must contain at least 32 UTF-8 bytes")

    expiration = datetime.now(timezone.utc) + timedelta(seconds=args.expires_in)
    print(jwt.encode(
        {"customer_id": args.customer_id.strip(), "exp": int(expiration.timestamp())},
        secret,
        algorithm="HS256",
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
