from datetime import datetime, timezone
from uuid import uuid4

from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.application.order_idempotency import validate_order_payload
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus


class CreateOrderUseCase:
    def __init__(self, order_repository: OrderRepositoryPort) -> None:
        self._order_repository = order_repository

    async def find_existing(
        self, operation_id: str, product_name: str, quantity: int
    ) -> Order | None:
        order = await self._order_repository.find_by_operation_id(operation_id)
        if order is not None:
            validate_order_payload(order, product_name.strip(), quantity)
        return order

    async def execute(
        self, product_name: str, quantity: int, operation_id: str
    ) -> Order:
        if not operation_id.strip():
            raise ValueError("L'identifiant d'operation est obligatoire.")
        normalized_product_name = product_name.strip()
        if not normalized_product_name:
            raise ValueError("Le nom du produit est obligatoire.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValueError("La quantité doit être au moins égale à 1.")

        order = Order(
            order_id=str(uuid4()),
            operation_id=operation_id,
            product_name=normalized_product_name,
            quantity=quantity,
            status=OrderStatus.CREATED,
            created_at=datetime.now(timezone.utc),
        )
        return await self._order_repository.create(order)
