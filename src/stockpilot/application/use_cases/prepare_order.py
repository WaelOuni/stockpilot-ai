from uuid import uuid4
from stockpilot.telemetry import traced
from stockpilot.metrics import record

from stockpilot.application.dto.prepared_order import PreparedOrder
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.domain.enums.order_status import PreparedOrderStatus


class PrepareOrderUseCase:
    def __init__(self, check_stock: CheckStockUseCase) -> None:
        self._check_stock = check_stock

    @traced("stockpilot.order.prepare")
    async def execute(
        self,
        product_name: str | None,
        quantity: int = 1,
    ) -> PreparedOrder:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            record("orders.prepared", outcome="invalid")
            raise ValueError("La quantité doit être un entier strictement positif.")

        if not product_name:
            record("orders.prepared", outcome="invalid")
            return PreparedOrder(
                product_name=None,
                quantity=quantity,
                status=PreparedOrderStatus.PRODUCT_NOT_FOUND,
            )

        stock = await self._check_stock.execute(product_name)

        if stock is None:
            record("orders.prepared", outcome="invalid")
            return PreparedOrder(
                product_name=product_name,
                quantity=quantity,
                status=PreparedOrderStatus.PRODUCT_NOT_FOUND,
            )

        if stock.stock == 0:
            record("orders.prepared", outcome="insufficient_stock")
            return PreparedOrder(
                product_name=stock.name,
                quantity=quantity,
                status=PreparedOrderStatus.OUT_OF_STOCK,
                available_stock=stock.stock,
            )

        if quantity > stock.stock:
            record("orders.prepared", outcome="insufficient_stock")
            return PreparedOrder(
                product_name=stock.name,
                quantity=quantity,
                status=PreparedOrderStatus.INSUFFICIENT_STOCK,
                available_stock=stock.stock,
            )

        record("orders.prepared", outcome="pending_confirmation")
        return PreparedOrder(
            product_name=stock.name,
            quantity=quantity,
            status=PreparedOrderStatus.PENDING_CONFIRMATION,
            available_stock=stock.stock,
            operation_id=str(uuid4()),
        )
