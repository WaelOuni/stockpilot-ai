from typing import Protocol

from stockpilot.domain.entities.order import Order


class OrderTransactionPort(Protocol):
    async def execute(self, order: Order) -> Order | None:
        """Atomically reserve and create, or return None for insufficient stock."""
        ...
