from typing import Protocol

from stockpilot.domain.entities.order import Order


class OrderTransactionPort(Protocol):
    async def execute(self, order: Order, *, replay_only: bool = False) -> Order | None:
        """Reserve/create atomically; replays must match product, quantity and customer.

        Return None for insufficient stock; a mismatched owner is an idempotency conflict.
        With replay_only, recover an existing operation or return None without a stock write.
        """
        ...
