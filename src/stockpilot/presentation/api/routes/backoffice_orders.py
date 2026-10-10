from fastapi import APIRouter, Depends, HTTPException, Query

from stockpilot.application.use_cases.backoffice_orders import (
    GetBackofficeOrderUseCase, ListBackofficeOrdersUseCase,
)
from stockpilot.presentation.api.admin_auth import require_backoffice_read
from stockpilot.presentation.api.dependencies import (
    get_backoffice_order_use_case, get_list_backoffice_orders_use_case,
)
from stockpilot.presentation.api.schemas.backoffice_order import (
    BackofficeOrderResponse, BackofficeOrdersResponse,
)


router = APIRouter(
    prefix="/backoffice/orders",
    dependencies=[Depends(require_backoffice_read)],
)


@router.get("", response_model=BackofficeOrdersResponse)
async def list_backoffice_orders(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    use_case: ListBackofficeOrdersUseCase = Depends(get_list_backoffice_orders_use_case),
) -> BackofficeOrdersResponse:
    orders = await use_case.execute(limit=limit, offset=offset)
    items = [BackofficeOrderResponse.model_validate(order) for order in orders]
    return BackofficeOrdersResponse(items=items, limit=limit, offset=offset, count=len(items))


@router.get("/{order_id}", response_model=BackofficeOrderResponse)
async def get_backoffice_order(
    order_id: str,
    use_case: GetBackofficeOrderUseCase = Depends(get_backoffice_order_use_case),
) -> BackofficeOrderResponse:
    order = await use_case.execute(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Commande introuvable (NOT_FOUND).")
    return BackofficeOrderResponse.model_validate(order)
