from typing import Protocol

from stockpilot.domain.entities.order import Order


class OrderRepositoryPort(Protocol):
    async def create(self, order: Order) -> Order:
        """Return the existing order for the same operation and payload."""
        ...

    async def find_by_operation_id(self, operation_id: str) -> Order | None:
        ...

    async def find_by_order_id(self, order_id: str) -> Order | None:
        ...
