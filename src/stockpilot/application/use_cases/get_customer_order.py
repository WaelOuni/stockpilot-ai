from stockpilot.application.customer_identity import validate_customer_id
from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.domain.entities.order import Order


class GetCustomerOrderUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self.order_repository = order_repository

    async def execute(self, order_id: str, customer_id: str) -> Order | None:
        if customer_id is None:
            raise ValueError("A customer identity is required.")
        validate_customer_id(customer_id)
        return await self.order_repository.find_by_order_id_and_customer_id(order_id, customer_id)
