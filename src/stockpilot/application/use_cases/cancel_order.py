from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.services.order_lifecycle import validate_order_cancellation


class CancelOrderUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self.order_repository = order_repository

    async def execute(self, order_id: str, *, customer_id: str | None = None) -> Order | None:
        if not order_id.strip():
            raise ValueError("L'identifiant de commande est obligatoire.")
        if customer_id is not None:
            if not customer_id.strip():
                raise ValueError("L'identifiant client est obligatoire.")
            order = await self.order_repository.find_by_order_id_and_customer_id(order_id, customer_id)
        else:
            order = await self.order_repository.find_by_order_id(order_id)
        if order is None:
            return None
        validate_order_cancellation(order.status)
        if order.status == OrderStatus.CANCELLED:
            return order
        if customer_id is not None:
            return await self.order_repository.cancel(
                order_id, expected_status=order.status, customer_id=customer_id,
            )
        return await self.order_repository.cancel(order_id, expected_status=order.status)
