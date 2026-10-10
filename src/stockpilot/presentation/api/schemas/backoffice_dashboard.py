from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from stockpilot.domain.enums.order_status import OrderStatus


class OrderDashboardStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_orders: int = Field(ge=0)
    by_status: dict[OrderStatus, int]


class ProductDashboardStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_products: int = Field(ge=0)
    available_products: int = Field(ge=0)
    unavailable_products: int = Field(ge=0)
    total_stock_units: int


class DashboardRecentOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    product_name: str
    quantity: int = Field(ge=1)
    status: OrderStatus
    created_at: datetime


class DashboardRecentStockMovementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_sku: str
    delta: int
    occurred_at: datetime
    reason: str


class BackofficeDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_stats: OrderDashboardStatsResponse
    product_stats: ProductDashboardStatsResponse
    recent_orders: list[DashboardRecentOrderResponse] = Field(max_length=5)
    recent_stock_movements: list[DashboardRecentStockMovementResponse] = Field(max_length=5)
