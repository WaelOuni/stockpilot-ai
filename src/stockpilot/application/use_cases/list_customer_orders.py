from stockpilot.application.customer_identity import validate_customer_id
from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.domain.entities.order import Order


class ListCustomerOrdersUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self.order_repository = order_repository

    async def execute(
        self, customer_id: str, limit: int = 20, offset: int = 0,
    ) -> list[Order]:
        validate_customer_id(customer_id)
        if customer_id is None:
            raise ValueError("L'identifiant client est obligatoire.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("La limite doit être comprise entre 1 et 100.")
        if type(offset) is not int or offset < 0:
            raise ValueError("Le décalage doit être un entier positif ou nul.")
        return await self.order_repository.find_by_customer_id(
            customer_id, limit=limit, offset=offset,
        )
