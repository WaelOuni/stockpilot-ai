from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.domain.entities.order import Order


class ListBackofficeOrdersUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self.order_repository = order_repository

    async def execute_page(self, limit: int = 20, offset: int = 0, *, q: str | None = None, customer_id: str | None = None):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("Invalid pagination.")
        if q is not None and (not isinstance(q, str) or len(q) > 100):
            raise ValueError("Invalid search.")
        return await self.order_repository.list_page(limit, offset, customer_id=customer_id, q=q)

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
