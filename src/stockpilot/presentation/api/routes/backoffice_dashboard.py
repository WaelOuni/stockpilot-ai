from fastapi import APIRouter, Depends

from stockpilot.application.use_cases.backoffice_dashboard import GetBackofficeDashboardUseCase
from stockpilot.presentation.api.admin_auth import require_admin_cookie_only
from stockpilot.presentation.api.dependencies import get_backoffice_dashboard_use_case
from stockpilot.presentation.api.schemas.backoffice_dashboard import BackofficeDashboardResponse


router = APIRouter(
    prefix="/backoffice/dashboard",
    tags=["backoffice dashboard"],
)


@router.get(
    "",
    response_model=BackofficeDashboardResponse,
    dependencies=[Depends(require_admin_cookie_only)],
)
async def get_backoffice_dashboard(
    use_case: GetBackofficeDashboardUseCase = Depends(get_backoffice_dashboard_use_case),
) -> BackofficeDashboardResponse:
    dashboard = await use_case.execute()
    return BackofficeDashboardResponse(
        order_stats={
            "total_orders": dashboard.order_stats.total_orders,
            "by_status": {
                status.value: count for status, count in dashboard.order_stats.by_status.items()
            },
        },
        product_stats={
            "total_products": dashboard.product_stats.total_products,
            "available_products": dashboard.product_stats.available_products,
            "unavailable_products": dashboard.product_stats.unavailable_products,
            "total_stock_units": dashboard.product_stats.total_stock_units,
        },
        recent_orders=[
            {
                "order_id": order.order_id,
                "product_name": order.product_name,
                "quantity": order.quantity,
                "status": order.status,
                "created_at": order.created_at,
            }
            for order in dashboard.recent_orders
        ],
        recent_stock_movements=[
            {
                "product_sku": movement.product_sku,
                "delta": movement.delta,
                "occurred_at": movement.occurred_at,
                "reason": movement.reason,
            }
            for movement in dashboard.recent_stock_movements
        ],
    )
