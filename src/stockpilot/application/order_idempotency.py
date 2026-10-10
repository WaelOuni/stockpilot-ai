import logging
from stockpilot.observability import event

from stockpilot.domain.entities.order import Order


logger = logging.getLogger(__name__)


class IdempotencyConflictError(ValueError):
    """An operation identifier was reused with a different business payload."""


def validate_order_payload(
    order: Order, product_name: str, quantity: int, customer_id: str | None = None
) -> None:
    if (
        order.product_name != product_name
        or order.quantity != quantity
        or order.customer_id != customer_id
    ):
        event(logger, "idempotency_conflict", level=logging.ERROR,
              operation_id=order.operation_id, message="Idempotency conflict")
        raise IdempotencyConflictError(
            "La cle d'idempotence correspond a une commande differente."
        )
