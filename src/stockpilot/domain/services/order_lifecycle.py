from stockpilot.domain.enums.order_status import OrderStatus


class InvalidOrderStatusTransitionError(ValueError):
    pass


_NEXT_STATUS = {
    OrderStatus.CREATED: OrderStatus.PROCESSING,
    OrderStatus.PROCESSING: OrderStatus.SHIPPED,
    OrderStatus.SHIPPED: OrderStatus.DELIVERED,
}


def validate_order_status_transition(current: OrderStatus, target: OrderStatus) -> None:
    if not isinstance(current, OrderStatus) or not isinstance(target, OrderStatus):
        raise TypeError("Order statuses must be OrderStatus values.")
    if current != target and _NEXT_STATUS.get(current) != target:
        raise InvalidOrderStatusTransitionError(
            f"Transition de statut interdite : {current.value} -> {target.value}."
        )


def validate_order_cancellation(status: OrderStatus) -> None:
    if status not in (OrderStatus.CREATED, OrderStatus.PROCESSING, OrderStatus.CANCELLED):
        raise InvalidOrderStatusTransitionError(
            f"Annulation interdite pour le statut {status.value}."
        )
