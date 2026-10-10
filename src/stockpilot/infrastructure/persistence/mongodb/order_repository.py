import logging
from stockpilot.observability import event
from datetime import datetime, timezone
from dataclasses import replace

from pymongo.errors import DuplicateKeyError
from pymongo import ReadPreference, ReturnDocument
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.order_idempotency import validate_order_payload
from stockpilot.application.ports.order_repository import OrderRepositoryPort
from stockpilot.application.use_cases.update_order_status import OrderStatusConflictError
from stockpilot.domain.entities.order import Order
from stockpilot.domain.entities.order_event import OrderEvent, OrderEventSource
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.services.order_lifecycle import validate_order_cancellation
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository


logger = logging.getLogger(__name__)


class MongoOrderRepository(OrderRepositoryPort):
    def __init__(self, database) -> None:
        self.collection = database["orders"]
        self._database = database

    def initialize(self) -> None:
        self.collection.create_index(
            [("order_id", 1)],
            name="orders_order_id_unique",
            unique=True,
        )
        self.collection.create_index(
            [("operation_id", 1)],
            name="orders_operation_id_unique",
            unique=True,
            partialFilterExpression={"operation_id": {"$type": "string"}},
        )
        self.collection.create_index(
            [("customer_id", 1), ("created_at", -1), ("order_id", -1)],
            name="orders_customer_created_order_desc",
        )

    async def find_by_operation_id(self, operation_id: str) -> Order | None:
        return self.find_by_operation_id_sync(operation_id)

    async def find_by_order_id(self, order_id: str) -> Order | None:
        document = self.collection.find_one({"order_id": order_id})
        return self._to_order(document) if document is not None else None

    async def find_by_customer_id(
        self, customer_id: str, limit: int, offset: int,
    ) -> list[Order]:
        cursor = (
            self.collection.find({"customer_id": customer_id})
            .sort([("created_at", -1), ("order_id", -1)])
            .skip(offset)
            .limit(limit)
        )
        return [self._to_order(document) for document in cursor]

    async def find_by_order_id_and_customer_id(
        self, order_id: str, customer_id: str,
    ) -> Order | None:
        document = self.collection.find_one({"order_id": order_id, "customer_id": customer_id})
        return self._to_order(document) if document is not None else None

    async def update_status(
        self, order_id: str, *, expected_status: OrderStatus, status: OrderStatus,
    ) -> Order | None:
        if expected_status == status:
            document = self.collection.find_one({"order_id": order_id, "status": status.value})
            return self._to_order(document) if document is not None else None
        document = self.collection.find_one_and_update(
            {"order_id": order_id, "status": expected_status.value},
            {
                "$set": {"status": status.value},
                "$push": {"events": self._event_document(OrderEvent(
                    status, datetime.now(timezone.utc), OrderEventSource.BACKOFFICE,
                ))},
            },
            return_document=ReturnDocument.AFTER,
        )
        return self._to_order(document) if document is not None else None

    async def cancel(
        self, order_id: str, *, expected_status: OrderStatus, customer_id: str | None = None,
    ) -> Order | None:
        if customer_id is not None and not customer_id.strip():
            raise ValueError("L'identifiant client est obligatoire.")
        order_filter = {"order_id": order_id}
        if customer_id is not None:
            order_filter["customer_id"] = customer_id

        def transaction(session):
            document = self.collection.find_one(order_filter, session=session)
            if document is None:
                return None
            order = self._to_order(document)
            if order.status == OrderStatus.CANCELLED:
                return order
            if order.status != expected_status:
                raise OrderStatusConflictError(
                    "La commande a changé avant son annulation ; relisez son statut."
                )
            validate_order_cancellation(order.status)
            if isinstance(order.quantity, bool) or not isinstance(order.quantity, int) or order.quantity < 1:
                raise ValueError("La quantité stockée est invalide pour la restitution.")
            if "reserved_product_id" in document:
                product_id = document["reserved_product_id"]
                if product_id is None or isinstance(product_id, (dict, list)):
                    raise RuntimeError("Identité du produit réservé invalide ; restitution refusée.")
            else:
                product = MongoProductRepository(self._database).resolve_stock_product(
                    order.product_name, session, legacy=True,
                )
                if product is None:
                    raise RuntimeError("Identité produit legacy introuvable ; restitution refusée.")
                product_id = product["_id"]
            stock_result = self._database["products"].update_one(
                {"_id": product_id},
                {"$inc": {"stock": order.quantity}},
                session=session,
            )
            if stock_result.matched_count != 1:
                raise RuntimeError("Le document produit réservé est introuvable pour la restitution.")
            updated = self.collection.find_one_and_update(
                {**order_filter, "status": expected_status.value},
                {
                    "$set": {"status": OrderStatus.CANCELLED.value},
                    "$push": {"events": self._event_document(OrderEvent(
                        OrderStatus.CANCELLED, datetime.now(timezone.utc),
                        OrderEventSource.CUSTOMER if customer_id is not None else OrderEventSource.BACKOFFICE,
                    ))},
                },
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated is None:
                raise OrderStatusConflictError(
                    "La commande a changé pendant son annulation ; relisez son statut."
                )
            return self._to_order(updated)

        with self._database.client.start_session() as session:
            return session.with_transaction(
                transaction,
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
                read_preference=ReadPreference.PRIMARY,
            )

    def find_by_operation_id_sync(self, operation_id: str, session=None) -> Order | None:
        session_options = {"session": session} if session is not None else {}
        # Explicitly imply the partial index predicate for Mongo's query planner.
        document = self.collection.find_one(
            {"operation_id": {"$eq": operation_id, "$type": "string"}}, **session_options
        )
        if document is None:
            return None
        return self._to_order(document)

    @staticmethod
    def _to_order(document) -> Order:
        return Order(
            order_id=document["order_id"],
            operation_id=document.get("operation_id", ""),
            product_name=document["product_name"],
            quantity=document["quantity"],
            status=OrderStatus(document["status"]),
            created_at=datetime.fromisoformat(document["created_at"]),
            customer_id=document.get("customer_id"),
            events=tuple(OrderEvent(
                status=OrderStatus(item["status"]),
                occurred_at=datetime.fromisoformat(item["occurred_at"]),
                source=OrderEventSource(item["source"]),
            ) for item in document.get("events", [])),
        )

    @staticmethod
    def _event_document(item: OrderEvent) -> dict:
        return {
            "status": item.status.value,
            "occurred_at": item.occurred_at.isoformat(),
            "source": item.source.value,
        }

    def insert(self, order: Order, session=None, *, reserved_product_id=None) -> Order:
        session_options = {"session": session} if session is not None else {}
        order = replace(order, events=(OrderEvent(
            OrderStatus.CREATED, order.created_at, OrderEventSource.SYSTEM,
        ),))
        self.collection.insert_one(
            {
                "order_id": order.order_id,
                "operation_id": order.operation_id,
                "product_name": order.product_name,
                "quantity": order.quantity,
                "status": order.status.value,
                "created_at": order.created_at.isoformat(),
                "events": [self._event_document(item) for item in order.events],
                **({"reserved_product_id": reserved_product_id} if reserved_product_id is not None else {}),
                **({"customer_id": order.customer_id} if order.customer_id is not None else {}),
            },
            **session_options,
        )
        return order

    async def create(self, order: Order) -> Order:
        try:
            return self.insert(order)
        except DuplicateKeyError:
            event(logger, "order_duplicate_detected", operation_id=order.operation_id)
            existing = await self.find_by_operation_id(order.operation_id)
            if existing is None:
                raise
            validate_order_payload(existing, order.product_name, order.quantity, order.customer_id)
            event(logger, "order_replay_detected", operation_id=order.operation_id,
                  order_id=existing.order_id)
            return existing
