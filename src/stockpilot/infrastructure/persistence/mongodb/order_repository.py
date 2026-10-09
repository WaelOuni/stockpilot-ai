import logging
from stockpilot.observability import event
from datetime import datetime

from pymongo.errors import DuplicateKeyError

from stockpilot.application.order_idempotency import validate_order_payload
from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus


logger = logging.getLogger(__name__)


class MongoOrderRepository(OrderRepositoryPort):
    def __init__(self, database) -> None:
        self.collection = database["orders"]

    def initialize(self) -> None:
        self.collection.create_index(
            [("operation_id", 1)],
            name="orders_operation_id_unique",
            unique=True,
            partialFilterExpression={"operation_id": {"$type": "string"}},
        )

    async def find_by_operation_id(self, operation_id: str) -> Order | None:
        return self.find_by_operation_id_sync(operation_id)

    def find_by_operation_id_sync(self, operation_id: str, session=None) -> Order | None:
        session_options = {"session": session} if session is not None else {}
        document = self.collection.find_one(
            {"operation_id": operation_id}, **session_options
        )
        if document is None:
            return None
        return Order(
            order_id=document["order_id"],
            operation_id=document["operation_id"],
            product_name=document["product_name"],
            quantity=document["quantity"],
            status=OrderStatus(document["status"]),
            created_at=datetime.fromisoformat(document["created_at"]),
        )

    def insert(self, order: Order, session=None) -> Order:
        session_options = {"session": session} if session is not None else {}
        self.collection.insert_one(
            {
                "order_id": order.order_id,
                "operation_id": order.operation_id,
                "product_name": order.product_name,
                "quantity": order.quantity,
                "status": order.status.value,
                "created_at": order.created_at.isoformat(),
            },
            **session_options,
        )
        return order

    async def create(self, order: Order) -> Order:
        try:
            self.insert(order)
        except DuplicateKeyError:
            event(logger, "order_duplicate_detected", operation_id=order.operation_id)
            existing = await self.find_by_operation_id(order.operation_id)
            if existing is None:
                raise
            validate_order_payload(existing, order.product_name, order.quantity)
            event(logger, "order_replay_detected", operation_id=order.operation_id,
                  order_id=existing.order_id)
            return existing
        return order
