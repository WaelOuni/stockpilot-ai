import asyncio

from stockpilot.application.dto.backoffice_dashboard import BackofficeDashboard
from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.application.ports.product_repository import ProductRepositoryPort


class GetBackofficeDashboardUseCase:
    def __init__(
        self,
        order_repository: OrderRepositoryPort,
        product_repository: ProductRepositoryPort,
    ) -> None:
        self.order_repository = order_repository
        self.product_repository = product_repository

    async def execute(self) -> BackofficeDashboard:
        order_stats, recent_orders, product_stats, recent_movements = await asyncio.gather(
            self.order_repository.get_dashboard_order_stats(),
            self.order_repository.list_recent_dashboard_orders(),
            self.product_repository.get_dashboard_product_stats(),
            self.product_repository.list_recent_dashboard_stock_movements(),
        )
        return BackofficeDashboard(
            order_stats=order_stats,
            product_stats=product_stats,
            recent_orders=tuple(recent_orders),
            recent_stock_movements=tuple(recent_movements),
        )
