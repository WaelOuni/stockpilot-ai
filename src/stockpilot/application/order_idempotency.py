import logging
from stockpilot.observability import event

from stockpilot.domain.entities.order import Order


logger = logging.getLogger(__name__)


class IdempotencyConflictError(ValueError):
    """An operation identifier was reused with a different business payload."""


def validate_order_payload(
    order: Order, product_name: str, quantity: int
) -> None:
    if order.product_name != product_name or order.quantity != quantity:
        event(logger, "idempotency_conflict", level=logging.ERROR,
              operation_id=order.operation_id, message="Idempotency conflict")
        raise IdempotencyConflictError(
            "La cle d'idempotence correspond a une commande differente."
        )
