"""A bounded page with a scoped, server-computed total."""
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


def pagination(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 10000:
        raise ValueError("Bounded pagination required.")


@dataclass(frozen=True)
class TenantPage(Generic[T]):
    items: tuple[T, ...]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self):
        return self.offset + self.limit < self.total
