from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.domain.entities.order import Order


class ListBackofficeOrdersUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self.order_repository = order_repository

    async def execute(self, limit: int = 20, offset: int = 0) -> list[Order]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be nonnegative.")
        return await self.order_repository.find_all(limit=limit, offset=offset)


class GetBackofficeOrderUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self.order_repository = order_repository

    async def execute(self, order_id: str) -> Order | None:
        return await self.order_repository.find_by_order_id(order_id)
