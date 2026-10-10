from fastapi import APIRouter, Depends, HTTPException, Path, Query

from stockpilot.application.use_cases.adjust_product_stock import (
    AdjustProductStockUseCase,
    GetStockAdjustmentUseCase,
    InsufficientStockError,
    ListStockMovementsUseCase,
    ProductStockNotFoundError,
    StockAdjustmentAmbiguousProductError,
    StockAdjustmentConflictError,
    StockAdjustmentOutcomeUnknownError,
    StockAdjustmentTransactionFailedError,
    StockMovementIndexNotReadyError,
)
from stockpilot.presentation.api.admin_auth import (
    SafeSessionRoute,
    require_admin_cookie_only,
    require_admin_mutation,
    require_backoffice_read,
)
from stockpilot.presentation.api.dependencies import (
    get_adjust_product_stock_use_case,
    get_list_stock_movements_use_case,
    get_stock_adjustment_use_case,
)
from stockpilot.presentation.api.schemas.backoffice_stock_movement import (
    StockAdjustmentRequest,
    StockMovementResponse,
    StockMovementsResponse,
)


class SafeStockRoute(SafeSessionRoute):
    validation_message = "Ajustement de stock invalide."


products_router = APIRouter(
    prefix="/backoffice/products",
    route_class=SafeStockRoute,
    tags=["backoffice stock"],
)
operations_router = APIRouter(
    prefix="/backoffice/stock-adjustments",
    route_class=SafeStockRoute,
    tags=["backoffice stock"],
)


def failure(error: Exception) -> HTTPException:
    if isinstance(error, (InsufficientStockError, StockAdjustmentConflictError,
                          StockAdjustmentAmbiguousProductError)):
        return HTTPException(409, "Ajustement refusé : stock insuffisant ou conflit d’opération.")
    if isinstance(error, ProductStockNotFoundError):
        return HTTPException(404, "Produit introuvable.")
    if isinstance(error, StockMovementIndexNotReadyError):
        return HTTPException(503, "Ajustement bloqué : index d’idempotence non provisionné.")
    if isinstance(error, StockAdjustmentOutcomeUnknownError):
        return HTTPException(503, "Résultat d’ajustement inconnu. Vérifiez l’opération avant toute nouvelle action.")
    if isinstance(error, StockAdjustmentTransactionFailedError):
        return HTTPException(500, "Transaction d’ajustement impossible.")
    return HTTPException(500, "Opération de stock impossible.")


@products_router.post(
    "/{sku}/stock-adjustments",
    response_model=StockMovementResponse,
    dependencies=[Depends(require_admin_mutation)],
)
async def adjust_stock(
    sku: str,
    request: StockAdjustmentRequest,
    use_case: AdjustProductStockUseCase = Depends(get_adjust_product_stock_use_case),
):
    try:
        movement = await use_case.execute(sku, request.delta, request.reason, request.operation_id)
        return StockMovementResponse.from_movement(movement)
    except Exception as error:
        raise failure(error) from error


@products_router.get(
    "/{sku}/stock-movements",
    response_model=StockMovementsResponse,
    dependencies=[Depends(require_backoffice_read)],
)
async def list_stock_movements(
    sku: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    use_case: ListStockMovementsUseCase = Depends(get_list_stock_movements_use_case),
):
    try:
        page = await use_case.execute(sku, limit, offset)
        items = [StockMovementResponse.from_movement(item) for item in page.items]
        return StockMovementsResponse(
            items=items, limit=limit, offset=offset, count=len(items), has_more=page.has_more,
        )
    except Exception as error:
        raise failure(error) from error


@operations_router.get(
    "/{operation_id}",
    response_model=StockMovementResponse,
    dependencies=[Depends(require_admin_cookie_only)],
)
async def get_stock_adjustment(
    operation_id: str = Path(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    ),
    use_case: GetStockAdjustmentUseCase = Depends(get_stock_adjustment_use_case),
):
    try:
        movement = await use_case.execute(operation_id)
    except Exception as error:
        raise failure(error) from error
    if movement is None:
        raise HTTPException(404, "Aucun mouvement visible pour cette opération.")
    return StockMovementResponse.from_movement(movement)
