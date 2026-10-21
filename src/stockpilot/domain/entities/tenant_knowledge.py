"""B2B knowledge contracts: no HTTP identity, driver or model dependency."""

import math
from dataclasses import dataclass

from stockpilot.domain.identity_validation import require_identifier

EMBEDDING_DIMENSION = 768


@dataclass(frozen=True)
class IngestionResult:
    operation_id: str
    document_id: str
    status: str
    revision: int


def text(value, maximum):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ValueError("Bounded nonempty text required.")
    value.encode("utf-8", errors="strict")
    return value


def vector(values):
    if not isinstance(values, (list, tuple)) or len(values) != EMBEDDING_DIMENSION:
        raise ValueError("Embedding dimension mismatch.")
    if any(
        type(v) not in (float, int) or abs(v) > 1e6 or not math.isfinite(v)
        for v in values
    ):
        raise ValueError("Finite numeric embedding required.")
    if max(abs(v) for v in values) < 1e-12:
        raise ValueError("Nonzero embedding required.")
    return tuple(float(v) for v in values)


@dataclass(frozen=True)
class IngestTenantDocument:
    operation_id: str
    document_id: str
    title: str
    content: str
    embedding_model: str
    embedding_version: str
    expected_revision: int

    def __post_init__(self):
        for v in (
            self.operation_id,
            self.document_id,
            self.embedding_model,
            self.embedding_version,
        ):
            require_identifier(v)
            if len(v) > 128 or "\x00" in v:
                raise ValueError("Identifier too long.")
        text(self.title, 200)
        text(self.content, 64000)
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise ValueError("Explicit nonnegative revision required.")


@dataclass(frozen=True)
class TenantDocument:
    organization_id: str
    document_id: str
    title: str
    revision: int
    status: str
    embedding_model: str
    embedding_version: str


@dataclass(frozen=True)
class TenantPassage:
    organization_id: str
    document_id: str
    chunk_id: int
    content: str
    distance: float


@dataclass(frozen=True)
class TenantConversation:
    organization_id: str
    user_id: str
    conversation_id: str
    external_thread_id: str
