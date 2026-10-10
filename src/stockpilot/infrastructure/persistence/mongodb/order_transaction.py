import logging
from stockpilot.observability import Timer, event, bind_context
from stockpilot.telemetry import traced, enrich_span
from stockpilot.metrics import observe_transaction, mark_transaction, order_replay

from pymongo import ReadPreference
from pymongo.errors import ConnectionFailure, DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.order_idempotency import validate_order_payload
from stockpilot.application.order_transaction_errors import (
    OrderOutcomeUnknownError,
    OrderTransactionFailedError,
)
from stockpilot.application.ports.order_transaction import OrderTransactionPort
from stockpilot.domain.entities.order import Order
from stockpilot.infrastructure.persistence.mongodb.order_repository import (
    MongoOrderRepository,
)
from stockpilot.infrastructure.persistence.mongodb.product_repository import (
    MongoProductRepository,
)


logger = logging.getLogger(__name__)


class _InsufficientStock(Exception):
    pass


class MongoOrderTransaction(OrderTransactionPort):
    def __init__(self, database) -> None:
        self.client = database.client
        self.products = MongoProductRepository(database)
        self.orders = MongoOrderRepository(database)

    def _existing(self, order: Order, session=None) -> Order | None:
        existing = self.orders.find_by_operation_id_sync(
            order.operation_id, session=session
        )
        if existing is not None:
            order_replay.set(True)
            validate_order_payload(existing, order.product_name, order.quantity, order.customer_id)
            event(logger, "order_replay_detected", operation_id=order.operation_id,
                  order_id=existing.order_id, message="Order replay recovered")
        return existing

    def _reconcile(self, order: Order) -> Order | None:
        try:
            return self._existing(order)
        except PyMongoError as error:
            event(logger, "order_reconciliation_failed", level=logging.WARNING,
                  operation_id=order.operation_id, error_type=type(error).__name__)
            raise OrderOutcomeUnknownError(
                "Impossible de verifier le resultat de la commande."
            ) from error

    @traced("stockpilot.mongo.order_transaction")
    @observe_transaction
    async def execute(self, order: Order) -> Order | None:
        enrich_span(**{
            "stockpilot.operation_id": order.operation_id,
            "stockpilot.product_name": order.product_name,
            "stockpilot.quantity": order.quantity,
        })
        bind_context(operation_id=order.operation_id)
        timer = Timer()
        existing = self._reconcile(order)
        if existing is not None:
            return existing

        def transaction(session):
            existing = self._existing(order, session)
            if existing is not None:
                return existing
            reservation = self.products.reserve_stock_document(
                order.product_name, order.quantity, session=session
            )
            if reservation is None:
                # Raising, rather than returning, makes with_transaction abort.
                raise _InsufficientStock
            return self.orders.insert(
                order, session=session, reserved_product_id=reservation["_id"],
            )

        try:
            mark_transaction(started=True)
            event(logger, "order_transaction_started", operation_id=order.operation_id,
                  product_name=order.product_name, quantity=order.quantity)
            with self.client.start_session() as session:
                result = session.with_transaction(
                    transaction,
                    read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"),
                    read_preference=ReadPreference.PRIMARY,
                )
                mark_transaction("committed")
                event(logger, "order_transaction_committed",
                      operation_id=order.operation_id, order_id=result.order_id,
                      product_name=order.product_name, quantity=order.quantity,
                      duration_ms=timer.duration_ms)
                return result
        except _InsufficientStock:
            mark_transaction("aborted")
            event(logger, "stock_reservation_failed", operation_id=order.operation_id,
                  product_name=order.product_name, quantity=order.quantity,
                  duration_ms=timer.duration_ms)
            # A concurrent transaction may have committed the same operation.
            return self._reconcile(order)
        except DuplicateKeyError as error:
            mark_transaction("aborted")
            existing = self._reconcile(order)
            if existing is not None:
                return existing
            raise OrderTransactionFailedError(
                "La transaction a echoue sans commande correspondante."
            ) from error
        except PyMongoError as error:
            existing = self._reconcile(order)
            if existing is not None:
                event(logger, "order_commit_recovered", operation_id=order.operation_id,
                      order_id=existing.order_id, duration_ms=timer.duration_ms,
                      message="Ambiguous order outcome recovered")
                return existing
            # with_transaction owns retries. Exhausted retries and network errors
            # are conservatively unknown; absence is not evidence of an abort.
            if (
                error.has_error_label("UnknownTransactionCommitResult")
                or error.has_error_label("TransientTransactionError")
                or isinstance(error, ConnectionFailure)
            ):
                event(logger, "order_commit_unresolved", level=logging.WARNING,
                      operation_id=order.operation_id, duration_ms=timer.duration_ms)
                raise OrderOutcomeUnknownError(
                    "Le resultat de la transaction est inconnu."
                ) from error
            raise OrderTransactionFailedError(
                "La transaction de commande a echoue."
            ) from error
