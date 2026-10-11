from fastapi import APIRouter, Depends, HTTPException, Query

from stockpilot.application.use_cases.backoffice_orders import (
    GetBackofficeOrderUseCase, ListBackofficeOrdersUseCase,
)
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.application.use_cases.update_order_status import (
    OrderStatusConflictError, UpdateOrderStatusUseCase,
)
from stockpilot.domain.services.order_lifecycle import InvalidOrderStatusTransitionError
from stockpilot.presentation.api.admin_auth import require_admin_mutation, require_backoffice_read
from stockpilot.presentation.api.dependencies import (
    get_backoffice_order_use_case, get_list_backoffice_orders_use_case,
    get_cancel_order_use_case, get_update_order_status_use_case,
)
from stockpilot.presentation.api.schemas.order import UpdateOrderStatusRequest
from stockpilot.presentation.api.schemas.backoffice_order import (
    BackofficeOrderResponse, BackofficeOrdersResponse,
)


router = APIRouter(prefix="/backoffice/orders")


@router.get("", response_model=BackofficeOrdersResponse, response_model_exclude_unset=True, dependencies=[Depends(require_backoffice_read)])
async def list_backoffice_orders(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_total: bool = Query(False),
    q: str | None = Query(None, max_length=100),
    use_case: ListBackofficeOrdersUseCase = Depends(get_list_backoffice_orders_use_case),
) -> BackofficeOrdersResponse:
    if include_total or q:
        page = await use_case.execute_page(limit, offset, q=q)
        items = [BackofficeOrderResponse.model_validate(order) for order in page.items]
        return BackofficeOrdersResponse(items=items, limit=limit, offset=offset, count=len(items), total=page.total, has_more=page.has_more)
    orders = await use_case.execute(limit=limit, offset=offset)
    items = [BackofficeOrderResponse.model_validate(order) for order in orders]
    return BackofficeOrdersResponse(items=items, limit=limit, offset=offset, count=len(items))


@router.get("/{order_id}", response_model=BackofficeOrderResponse, dependencies=[Depends(require_backoffice_read)])
async def get_backoffice_order(
    order_id: str,
    use_case: GetBackofficeOrderUseCase = Depends(get_backoffice_order_use_case),
) -> BackofficeOrderResponse:
    order = await use_case.execute(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Commande introuvable (NOT_FOUND).")
    return BackofficeOrderResponse.model_validate(order)


@router.patch("/{order_id}/status", response_model=BackofficeOrderResponse,
              dependencies=[Depends(require_admin_mutation)])
async def update_backoffice_order_status(
    order_id: str,
    request: UpdateOrderStatusRequest,
    use_case: UpdateOrderStatusUseCase = Depends(get_update_order_status_use_case),
) -> BackofficeOrderResponse:
    try:
        order = await use_case.execute(order_id, request.status)
    except (InvalidOrderStatusTransitionError, OrderStatusConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if order is None:
        raise HTTPException(status_code=404, detail="Commande introuvable (NOT_FOUND).")
    return BackofficeOrderResponse.model_validate(order)


@router.post("/{order_id}/cancel", response_model=BackofficeOrderResponse,
             dependencies=[Depends(require_admin_mutation)])
async def cancel_backoffice_order(
    order_id: str,
    use_case: CancelOrderUseCase = Depends(get_cancel_order_use_case),
) -> BackofficeOrderResponse:
    try:
        order = await use_case.execute(order_id)
    except (InvalidOrderStatusTransitionError, OrderStatusConflictError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if order is None:
        raise HTTPException(status_code=404, detail="Commande introuvable (NOT_FOUND).")
    return BackofficeOrderResponse.model_validate(order)
