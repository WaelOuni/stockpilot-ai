"""Plain-text tenant contact requests, separate from orders and assistant."""
from dataclasses import dataclass
from stockpilot.domain.identity_validation import require_identifier

CATEGORIES = ("access", "catalogue", "order", "stock", "other")


def text(value, maximum, *, multiline=False):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Bounded nonempty contact text required.")
    if any((ord(c) < 32 and not (multiline and c in "\n\t")) or ord(c) == 127 for c in value):
        raise ValueError("Invalid control character.")
    return value


@dataclass(frozen=True)
class CreateContact:
    operation_id: str
    subject: str
    category: str
    description: str

    def __post_init__(self):
        require_identifier(self.operation_id)
        text(self.subject, 120)
        text(self.description, 4000, multiline=True)
        if self.category not in CATEGORIES:
            raise ValueError("Known contact category required.")


@dataclass(frozen=True)
class ReplyContact:
    operation_id: str
    request_id: str
    body: str

    def __post_init__(self):
        require_identifier(self.operation_id)
        require_identifier(self.request_id)
        text(self.body, 4000, multiline=True)


@dataclass(frozen=True)
class SetContactStatus:
    operation_id: str
    request_id: str
    status: str

    def __post_init__(self):
        require_identifier(self.operation_id)
        require_identifier(self.request_id)
        if self.status not in ("open", "resolved"):
            raise ValueError("Known contact status required.")


@dataclass(frozen=True)
class ContactQuery:
    limit: int = 20
    offset: int = 0
    scope: str = "own"
    status: str | None = None

    def __post_init__(self):
        if type(self.limit) is not int or not 1 <= self.limit <= 100 or type(self.offset) is not int or not 0 <= self.offset <= 10000:
            raise ValueError("Bounded pagination required.")
        if self.scope not in ("own", "organization") or self.status not in (None, "open", "resolved"):
            raise ValueError("Known contact scope/status required.")
