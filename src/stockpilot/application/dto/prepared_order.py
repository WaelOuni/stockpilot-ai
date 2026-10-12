from dataclasses import dataclass, field

from stockpilot.domain.enums.order_status import PreparedOrderStatus


@dataclass(frozen=True)
class PreparedOrder:
    product_name: str | None
    quantity: int
    status: PreparedOrderStatus
    available_stock: int | None = None
    operation_id: str | None = None
    product_sku: str | None = None
    product_document_id: str | None = field(default=None, repr=False)
