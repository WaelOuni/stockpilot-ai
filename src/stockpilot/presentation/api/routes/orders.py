from fastapi import APIRouter, Depends, HTTPException, Query

from stockpilot.application.use_cases.update_order_status import (
    OrderStatusConflictError,
    UpdateOrderStatusUseCase,
)
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.application.use_cases.list_customer_orders import ListCustomerOrdersUseCase
from stockpilot.application.use_cases.get_customer_order import GetCustomerOrderUseCase
from stockpilot.domain.services.order_lifecycle import InvalidOrderStatusTransitionError
from stockpilot.presentation.api.auth import require_backoffice_token, require_customer_identity
from stockpilot.presentation.api.dependencies import (
    get_cancel_order_use_case,
    get_update_order_status_use_case,
    get_list_customer_orders_use_case,
    get_customer_order_use_case,
)
from stockpilot.presentation.api.schemas.order import (
    CustomerOrdersResponse, OrderResponse, UpdateOrderStatusRequest,
)


router = APIRouter()


@router.post("/customer/orders/{order_id}/cancel", response_model=OrderResponse)
async def cancel_customer_order(
    order_id: str,
    customer_id: str = Depends(require_customer_identity),
    use_case: CancelOrderUseCase = Depends(get_cancel_order_use_case),
) -> OrderResponse:
    try:
        order = await use_case.execute(order_id, customer_id=customer_id)
    except (InvalidOrderStatusTransitionError, OrderStatusConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if order is None:
        raise HTTPException(status_code=404, detail="Commande introuvable (NOT_FOUND).")
    return OrderResponse.model_validate(order)


@router.get("/orders/{order_id}", response_model=OrderResponse)
async def get_customer_order(
    order_id: str,
    customer_id: str = Depends(require_customer_identity),
    use_case: GetCustomerOrderUseCase = Depends(get_customer_order_use_case),
) -> OrderResponse:
    order = await use_case.execute(order_id, customer_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Commande introuvable (NOT_FOUND).")
    return OrderResponse.model_validate(order)


@router.get("/orders", response_model=CustomerOrdersResponse)
async def list_customer_orders(
    customer_id: str = Depends(require_customer_identity),
    use_case: ListCustomerOrdersUseCase = Depends(get_list_customer_orders_use_case),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> CustomerOrdersResponse:
    orders = await use_case.execute(customer_id, limit=limit, offset=offset)
    items = [OrderResponse.model_validate(order) for order in orders]
    return CustomerOrdersResponse(items=items, limit=limit, offset=offset, count=len(items))


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
