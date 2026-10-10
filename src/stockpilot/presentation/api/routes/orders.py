from fastapi import APIRouter, Depends, HTTPException

from stockpilot.application.use_cases.update_order_status import (
    OrderStatusConflictError,
    UpdateOrderStatusUseCase,
)
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.domain.services.order_lifecycle import InvalidOrderStatusTransitionError
from stockpilot.presentation.api.auth import require_backoffice_token
from stockpilot.presentation.api.dependencies import (
    get_cancel_order_use_case,
    get_update_order_status_use_case,
)
from stockpilot.presentation.api.schemas.order import OrderResponse, UpdateOrderStatusRequest


router = APIRouter()


@router.patch(
    "/orders/{order_id}/status",
    response_model=OrderResponse,
    dependencies=[Depends(require_backoffice_token)],
)
async def update_order_status(
    order_id: str,
    request: UpdateOrderStatusRequest,
    use_case: UpdateOrderStatusUseCase = Depends(get_update_order_status_use_case),
) -> OrderResponse:
    try:
        order = await use_case.execute(order_id, request.status)
    except (InvalidOrderStatusTransitionError, OrderStatusConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if order is None:
        raise HTTPException(status_code=404, detail="Commande introuvable (NOT_FOUND).")
    return OrderResponse.model_validate(order)


@router.post(
    "/orders/{order_id}/cancel",
    response_model=OrderResponse,
    dependencies=[Depends(require_backoffice_token)],
)
async def cancel_order(
    order_id: str,
    use_case: CancelOrderUseCase = Depends(get_cancel_order_use_case),
) -> OrderResponse:
    try:
        order = await use_case.execute(order_id)
    except (InvalidOrderStatusTransitionError, OrderStatusConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if order is None:
        raise HTTPException(status_code=404, detail="Commande introuvable (NOT_FOUND).")
    return OrderResponse.model_validate(order)
