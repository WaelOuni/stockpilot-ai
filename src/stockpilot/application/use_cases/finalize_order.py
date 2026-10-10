from datetime import datetime, timezone
from uuid import uuid4
from stockpilot.telemetry import traced, enrich_span
from stockpilot.metrics import record, order_replay
from stockpilot.application.order_idempotency import IdempotencyConflictError
from stockpilot.application.customer_identity import validate_customer_id
from stockpilot.application.order_transaction_errors import (
    OrderOutcomeUnknownError, OrderTransactionFailedError,
)

from stockpilot.application.ports.order_transaction import OrderTransactionPort
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus


class FinalizeOrderUseCase:
    def __init__(self, transaction: OrderTransactionPort) -> None:
        self._transaction = transaction

    @traced("stockpilot.order.finalize")
    async def execute(
        self, product_name: str, quantity: int, operation_id: str,
        customer_id: str | None = None,
    ) -> Order | None:
        validate_customer_id(customer_id)
        normalized_name = product_name.strip()
        enrich_span(**{
            "stockpilot.operation_id": operation_id,
            "stockpilot.product_name": normalized_name,
            "stockpilot.quantity": quantity,
        })
        if not normalized_name:
            raise ValueError("Le nom du produit est obligatoire.")
        if not operation_id.strip():
            raise ValueError("L'identifiant d'operation est obligatoire.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValueError("La quantite doit etre un entier strictement positif.")
        order = Order(
            order_id=str(uuid4()),
            operation_id=operation_id,
            product_name=normalized_name,
            quantity=quantity,
            status=OrderStatus.CREATED,
            created_at=datetime.now(timezone.utc),
            customer_id=customer_id,
        )
        token = order_replay.set(False)
        try:
            result = await self._transaction.execute(order)
            if result is None:
                record("orders.failed", reason="reservation_failed")
            else:
                record("orders.finalized", outcome="replay" if order_replay.get() else "confirmed")
        except IdempotencyConflictError:
            record("orders.failed", reason="idempotency_conflict")
            raise
        except OrderOutcomeUnknownError:
            record("orders.failed", reason="outcome_unknown")
            raise
        except OrderTransactionFailedError:
            record("orders.failed", reason="creation_failed")
            raise
        finally:
            order_replay.reset(token)
        enrich_span(**{
            "stockpilot.order_status": "CONFIRMED" if result else "RESERVATION_FAILED",
            "stockpilot.order_id": result.order_id if result else None,
        })
        return result
