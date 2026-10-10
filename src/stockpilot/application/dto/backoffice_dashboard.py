from dataclasses import dataclass
from datetime import datetime

from stockpilot.domain.enums.order_status import OrderStatus


@dataclass(frozen=True)
class OrderDashboardStats:
    total_orders: int
    by_status: dict[OrderStatus, int]


@dataclass(frozen=True)
class ProductDashboardStats:
    total_products: int
    available_products: int
    unavailable_products: int
    total_stock_units: int


@dataclass(frozen=True)
class DashboardRecentOrder:
    order_id: str
    product_name: str
    quantity: int
    status: OrderStatus
    created_at: datetime


@dataclass(frozen=True)
class DashboardRecentStockMovement:
    product_sku: str
    delta: int
    occurred_at: datetime
    reason: str


@dataclass(frozen=True)
class BackofficeDashboard:
    order_stats: OrderDashboardStats
    product_stats: ProductDashboardStats
    recent_orders: tuple[DashboardRecentOrder, ...]
    recent_stock_movements: tuple[DashboardRecentStockMovement, ...]
