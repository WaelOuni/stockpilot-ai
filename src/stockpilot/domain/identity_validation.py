"""Validation shared by the inactive B2B domain contracts."""

from datetime import datetime


def require_identifier(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            "Identifiers must be nonblank strings without surrounding whitespace."
        )


def require_dates(created_at: datetime, updated_at: datetime) -> None:
    for value in (created_at, updated_at):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Identity timestamps must be timezone-aware.")
    if updated_at < created_at:
        raise ValueError("updated_at cannot precede created_at.")
