from stockpilot.application.dto.order_status_result import OrderStatusResult
from stockpilot.application.ports.order_repository import OrderRepositoryPort


class GetOrderStatusUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self.order_repository = order_repository

    async def execute(
        self, order_id: str | None, customer_id: str | None = None,
    ) -> OrderStatusResult:
        if order_id is None:
            return OrderStatusResult(
                order_id=None,
                status=None,
                result="NO_ORDER_ASSOCIATED",
                response="Aucune commande n'est associée à cette conversation.",
            )

        order = await self.order_repository.find_by_order_id(order_id)
        if (
            order is None
            or not isinstance(customer_id, str)
            or not customer_id.strip()
            or not isinstance(order.customer_id, str)
            or not order.customer_id.strip()
            or order.customer_id != customer_id
        ):
            return OrderStatusResult(
                order_id=order_id,
                status=None,
                result="NOT_FOUND",
                response=f"Commande {order_id} introuvable (NOT_FOUND).",
            )

        return OrderStatusResult(
            order_id=order.order_id,
            status=order.status,
            result="FOUND",
            response=f"Commande {order.order_id} : statut {order.status.value}.",
        )
