from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.services.order_lifecycle import validate_order_status_transition


class OrderStatusConflictError(RuntimeError):
    pass


class UpdateOrderStatusUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self.order_repository = order_repository

    async def execute(self, order_id: str, status: OrderStatus) -> Order | None:
        if not order_id.strip():
            raise ValueError("L'identifiant de commande est obligatoire.")
        if not isinstance(status, OrderStatus):
            raise TypeError("Le statut doit être une valeur OrderStatus.")

        order = await self.order_repository.find_by_order_id(order_id)
        if order is None:
            return None

        validate_order_status_transition(order.status, status)
        if order.status == status:
            return order

        updated = await self.order_repository.update_status(
            order_id, expected_status=order.status, status=status,
        )
        if updated is None:
            raise OrderStatusConflictError(
                "La commande a changé pendant la mise à jour ; relisez son statut."
            )
        return updated
