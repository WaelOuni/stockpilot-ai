from typing import Protocol

from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus


class OrderRepositoryPort(Protocol):
    async def create(self, order: Order) -> Order:
        """Replay only the same operation, product, quantity and customer (including None)."""
        ...

    async def find_by_operation_id(self, operation_id: str) -> Order | None:
        ...

    async def find_by_order_id(self, order_id: str) -> Order | None:
        ...

    async def find_by_order_id_and_customer_id(
        self, order_id: str, customer_id: str,
    ) -> Order | None:
        """Return only an order owned by the exact nonblank customer."""
        ...

    async def find_by_customer_id(
        self, customer_id: str, limit: int, offset: int,
    ) -> list[Order]:
        """Filter by the exact customer before pagination."""
        ...

    async def update_status(
        self, order_id: str, *, expected_status: OrderStatus, status: OrderStatus,
    ) -> Order | None:
        """Update only if the stored status matches; return None on mismatch/absence."""
        ...

    async def cancel(self, order_id: str, *, expected_status: OrderStatus) -> Order | None:
        """Atomically restore stock and cancel, or return an already cancelled order."""
        ...
