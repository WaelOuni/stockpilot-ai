"""Personal visibility preferences; never ORDER state transitions."""
from dataclasses import dataclass
from stockpilot.domain.entities.tenant_order import identifier
from stockpilot.domain.entities.tenant_page import pagination


@dataclass(frozen=True)
class OrderListQuery:
    limit: int = 20
    offset: int = 0
    status: str | None = None
    archived: bool | None = False

    def __post_init__(self):
        pagination(self.limit, self.offset)
        if self.status not in (None, "prepared", "reserved", "cancelled"):
            raise ValueError("Known order status required.")
        if self.archived is not None and type(self.archived) is not bool:
            raise ValueError("Boolean archive selector required.")


@dataclass(frozen=True)
class SetOrderArchive:
    operation_id: str
    order_id: str
    archived: bool
    confirmed: bool

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.order_id)
        if type(self.archived) is not bool or self.confirmed is not True:
            raise ValueError("Explicit archive confirmation required.")
