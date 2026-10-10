"""Deterministic, synthetic StockPilot demo fixture generation (in memory only)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from bson import ObjectId

from stockpilot.domain.entities.order_event import OrderEventSource
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.domain.enums.product_category import ProductCategory


DEMO_DATABASE = "stockpilot_demo"
DEFAULT_SEED = 20261010
DATASET_VERSION = 1
PRODUCT_COUNT = 500
ORDER_COUNT = 2000
MOVEMENT_COUNT = 3000
CUSTOMER_COUNT = 100
BASE_TIME = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
ORDER_EVENT_SOURCES = {source.value for source in OrderEventSource}
MOVEMENT_SOURCE = "BACKOFFICE"
PRODUCT_BRANDS = (
    "Northline Tools",
    "Alderstone Workshop",
    "Forgefield Industrial",
    "Cairn & Timber",
    "Redwood Works",
)
PRODUCT_LINES = {
    ProductCategory.DRILL: (
        "18V Brushless Drill",
        "Compact Impact Drill",
        "Corded Hammer Drill",
        "Right-Angle Drill",
        "Rotary Hammer",
    ),
    ProductCategory.SAW: (
        "Circular Saw",
        "Compact Jigsaw",
        "Reciprocating Saw",
        "Sliding Mitre Saw",
        "Portable Table Saw",
    ),
    ProductCategory.SANDER: (
        "Random-Orbit Sander",
        "Belt Sander",
        "Detail Sander",
        "Finishing Sander",
        "Compact Disc Sander",
    ),
    ProductCategory.SCREWDRIVER: (
        "12V Impact Driver",
        "Cordless Screwdriver",
        "Precision Driver Set",
        "Right-Angle Impact Driver",
        "Brushless Impact Driver",
    ),
    ProductCategory.OTHER: (
        "Angle Grinder",
        "Workshop Charger",
        "Portable Work Light",
        "Bench Grinder",
        "Tool Battery Pack",
    ),
}
PRODUCT_PRICE_RANGES = {
    ProductCategory.DRILL: (4999, 44999),
    ProductCategory.SAW: (3999, 79999),
    ProductCategory.SANDER: (2499, 39999),
    ProductCategory.SCREWDRIVER: (1499, 29999),
    ProductCategory.OTHER: (999, 49999),
}

PLANNED_INDEXES = (
    ("products_sku_unique", (("sku", 1),), True, None),
    ("orders_order_id_unique", (("order_id", 1),), True, None),
    (
        "orders_operation_id_unique",
        (("operation_id", 1),),
        True,
        {"operation_id": {"$type": "string"}},
    ),
    (
        "orders_customer_created_order_desc",
        (("customer_id", 1), ("created_at", -1), ("order_id", -1)),
        False,
        None,
    ),
    ("stock_movements_operation_id_unique", (("operation_id", 1),), True, None),
    (
        "stock_movements_product_history",
        (("product_sku", 1), ("occurred_at", -1), ("movement_id", -1)),
        False,
        None,
    ),
)


@dataclass(frozen=True)
class DemoDataset:
    dataset_id: str
    seed: int
    products: tuple[dict[str, Any], ...]
    orders: tuple[dict[str, Any], ...]
    stock_movements: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]
    opening_stock_by_sku: dict[str, int]
    stock_after_orders_by_sku: dict[str, int]


def _digest(seed: int, label: str) -> bytes:
    return hashlib.sha256(f"stockpilot-demo-v{DATASET_VERSION}:{seed}:{label}".encode()).digest()


def _integer(seed: int, label: str, minimum: int, maximum: int) -> int:
    return minimum + int.from_bytes(_digest(seed, label)[:8], "big") % (maximum - minimum + 1)


def _uuid(seed: int, label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"stockpilot-demo-v{DATASET_VERSION}:{seed}:{label}"))


def _object_id(seed: int, label: str) -> ObjectId:
    return ObjectId(_digest(seed, label)[:12])


def _iso(value: datetime) -> str:
    return value.isoformat()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return {"$oid": str(value)}
    if isinstance(value, datetime):
        return {"$date": value.isoformat()}
    raise TypeError(f"Unsupported deterministic dataset value: {type(value).__name__}")


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_canonical_value,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _order_status(index: int) -> OrderStatus:
    bucket = index % 10
    if bucket <= 5:
        return OrderStatus.DELIVERED
    if bucket == 6:
        return OrderStatus.SHIPPED
    if bucket == 7:
        return OrderStatus.PROCESSING
    if bucket == 8:
        return OrderStatus.CREATED
    return OrderStatus.CANCELLED


def _order_events(index: int, status: OrderStatus, created_at: datetime) -> list[dict[str, str]]:
    statuses: list[OrderStatus]
    if status == OrderStatus.DELIVERED:
        statuses = [
            OrderStatus.CREATED, OrderStatus.PROCESSING, OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
        ]
    elif status == OrderStatus.SHIPPED:
        statuses = [OrderStatus.CREATED, OrderStatus.PROCESSING, OrderStatus.SHIPPED]
    elif status == OrderStatus.PROCESSING:
        statuses = [OrderStatus.CREATED, OrderStatus.PROCESSING]
    elif status == OrderStatus.CANCELLED and index % 2:
        statuses = [OrderStatus.CREATED, OrderStatus.PROCESSING, OrderStatus.CANCELLED]
    elif status == OrderStatus.CANCELLED:
        statuses = [OrderStatus.CREATED, OrderStatus.CANCELLED]
    else:
        statuses = [OrderStatus.CREATED]

    events = []
    for offset, event_status in enumerate(statuses):
        source = (
            OrderEventSource.SYSTEM.value
            if offset == 0
            else OrderEventSource.BACKOFFICE.value
        )
        events.append({
            "status": event_status.value,
            "occurred_at": _iso(created_at + timedelta(hours=offset)),
            "source": source,
        })
    return events


def generate_demo_dataset(seed: int = DEFAULT_SEED) -> DemoDataset:
    """Build the complete synthetic dataset in memory; performs no database I/O."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("Seed must be a non-negative integer.")

    dataset_id = f"stockpilot-demo-v{DATASET_VERSION}-seed-{seed}"
    categories = tuple(ProductCategory)
    products: list[dict[str, Any]] = []
    product_by_sku: dict[str, dict[str, Any]] = {}
    opening_stock_by_sku: dict[str, int] = {}

    for index in range(PRODUCT_COUNT):
        category = categories[index % len(categories)]
        sku = f"DEMO-{category.value}-{index + 1:04d}"
        brand = PRODUCT_BRANDS[(index // len(categories)) % len(PRODUCT_BRANDS)]
        product_line = PRODUCT_LINES[category][(index // len(categories)) % len(PRODUCT_LINES[category])]
        minimum_price, maximum_price = PRODUCT_PRICE_RANGES[category]
        product = {
            "_id": _object_id(seed, f"product:{sku}"),
            "sku": sku,
            "name": f"{brand} {product_line} {index + 1:04d}",
            "category": category.value,
            "price": round(_integer(seed, f"price:{sku}", minimum_price, maximum_price) / 100, 2),
            "stock": 0,
            "demo_dataset_id": dataset_id,
        }
        products.append(product)
        product_by_sku[sku] = product
        opening_stock_by_sku[sku] = _integer(seed, f"opening-stock:{sku}", 45, 140)

    inventory = dict(opening_stock_by_sku)
    orders: list[dict[str, Any]] = []
    for index in range(ORDER_COUNT):
        product = products[index % PRODUCT_COUNT]
        sku = product["sku"]
        quantity = 1 + index % 3
        status = _order_status(index)
        created_at = BASE_TIME - timedelta(days=180) + timedelta(hours=index)
        order_id = _uuid(seed, f"order-id:{index}")
        operation_id = _uuid(seed, f"order-operation:{index}")
        inventory[sku] -= quantity
        if status == OrderStatus.CANCELLED:
            inventory[sku] += quantity
        orders.append({
            "_id": _object_id(seed, f"order:{index}"),
            "order_id": order_id,
            "operation_id": operation_id,
            "product_name": product["name"],
            "quantity": quantity,
            "status": status.value,
            "created_at": _iso(created_at),
            "events": _order_events(index, status, created_at),
            "reserved_product_id": product["_id"],
            "customer_id": f"demo-customer-{index % CUSTOMER_COUNT + 1:03d}",
            "demo_dataset_id": dataset_id,
        })

    stock_after_orders_by_sku = dict(inventory)
    movements: list[dict[str, Any]] = []
    movement_start = BASE_TIME + timedelta(days=1)
    for index in range(MOVEMENT_COUNT):
        product = products[index % PRODUCT_COUNT]
        sku = product["sku"]
        current_stock = inventory[sku]
        magnitude = 1 + index % 5
        delta = magnitude if index % 2 == 0 else -magnitude
        if current_stock + delta < 0:
            delta = current_stock
        after = current_stock + delta
        occurred_at = movement_start + timedelta(minutes=15 * index)
        movements.append({
            "_id": _object_id(seed, f"movement:{index}"),
            "movement_id": _uuid(seed, f"movement-id:{index}"),
            "operation_id": _uuid(seed, f"movement-operation:{index}"),
            "product_sku": sku,
            "product_id": product["_id"],
            "delta": delta,
            "stock_before": current_stock,
            "stock_after": after,
            "reason": "Synthetic demo inventory adjustment",
            "occurred_at": occurred_at,
            "source": MOVEMENT_SOURCE,
            "demo_dataset_id": dataset_id,
        })
        inventory[sku] = after

    for sku, stock in inventory.items():
        product_by_sku[sku]["stock"] = stock

    payload = {
        "dataset_id": dataset_id,
        "seed": seed,
        "products": products,
        "orders": orders,
        "stock_movements": movements,
    }
    manifest = {
        "_id": dataset_id,
        "dataset_id": dataset_id,
        "version": DATASET_VERSION,
        "seed": seed,
        "prepared_at": BASE_TIME,
        "collections": {
            "products": PRODUCT_COUNT,
            "orders": ORDER_COUNT,
            "stock_movements": MOVEMENT_COUNT,
        },
        "inventory_model": {
            "opening_stock_units": sum(opening_stock_by_sku.values()),
            "opening_stock_per_product": "seeded 45..140 units",
            "order_reservations": "one per order; cancellation returns exact quantity",
            "complete_inventory_ledger": False,
        },
        "sha256": _fingerprint(payload),
    }
    dataset = DemoDataset(
        dataset_id=dataset_id,
        seed=seed,
        products=tuple(products),
        orders=tuple(orders),
        stock_movements=tuple(movements),
        manifest=manifest,
        opening_stock_by_sku=opening_stock_by_sku,
        stock_after_orders_by_sku=stock_after_orders_by_sku,
    )
    validate_demo_dataset(dataset)
    return dataset


def validate_demo_dataset(dataset: DemoDataset) -> None:
    """Validate references, status histories, and per-SKU stock arithmetic."""
    if (
        len(dataset.products) != PRODUCT_COUNT
        or len(dataset.orders) != ORDER_COUNT
        or len(dataset.stock_movements) != MOVEMENT_COUNT
    ):
        raise ValueError("Generated dataset counts do not match the demo profile.")

    products_by_id = {product["_id"]: product for product in dataset.products}
    products_by_sku = {product["sku"]: product for product in dataset.products}
    if len(products_by_id) != PRODUCT_COUNT or len(products_by_sku) != PRODUCT_COUNT:
        raise ValueError("Generated product identifiers are not unique.")
    if any(product["category"] not in ProductCategory._value2member_map_ for product in dataset.products):
        raise ValueError("Generated product category is not supported.")

    if len({order["order_id"] for order in dataset.orders}) != ORDER_COUNT:
        raise ValueError("Generated order identifiers are not unique.")
    if len({order["operation_id"] for order in dataset.orders}) != ORDER_COUNT:
        raise ValueError("Generated order operation identifiers are not unique.")

    net_reserved: dict[str, int] = {sku: 0 for sku in products_by_sku}
    for order in dataset.orders:
        status = OrderStatus(order["status"])
        if order["reserved_product_id"] not in products_by_id:
            raise ValueError("Generated order has no matching product identity.")
        product = products_by_id[order["reserved_product_id"]]
        if order["product_name"] != product["name"]:
            raise ValueError("Generated order product name does not match its reserved product.")
        if order["customer_id"] not in {
            f"demo-customer-{index:03d}" for index in range(1, CUSTOMER_COUNT + 1)
        }:
            raise ValueError("Generated order customer is outside the synthetic customer set.")
        events = order["events"]
        event_statuses = [OrderStatus(event["status"]) for event in events]
        if not event_statuses or event_statuses[0] != OrderStatus.CREATED or event_statuses[-1] != status:
            raise ValueError("Generated order event history does not match its status.")
        if any(event["source"] not in ORDER_EVENT_SOURCES for event in events):
            raise ValueError("Generated order event source is not supported.")
        if any(
            later <= earlier
            for earlier, later in zip(
                (datetime.fromisoformat(event["occurred_at"]) for event in events),
                (datetime.fromisoformat(event["occurred_at"]) for event in events[1:]),
            )
        ):
            raise ValueError("Generated order event timestamps are not increasing.")
        for previous, target in zip(event_statuses, event_statuses[1:]):
            legal = {
                OrderStatus.CREATED: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
                OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
                OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
                OrderStatus.DELIVERED: set(),
                OrderStatus.CANCELLED: set(),
            }
            if target not in legal[previous]:
                raise ValueError("Generated order contains an invalid lifecycle transition.")
        if status != OrderStatus.CANCELLED:
            net_reserved[product["sku"]] += order["quantity"]

    expected_stock = {
        sku: opening - net_reserved[sku]
        for sku, opening in dataset.opening_stock_by_sku.items()
    }
    if expected_stock != dataset.stock_after_orders_by_sku:
        raise ValueError("Generated order reservations and cancellations do not balance.")

    movement_ids: set[str] = set()
    operation_ids: set[str] = set()
    for movement in dataset.stock_movements:
        sku = movement["product_sku"]
        if sku not in products_by_sku or movement["product_id"] != products_by_sku[sku]["_id"]:
            raise ValueError("Generated stock movement has no matching product.")
        if movement["source"] != MOVEMENT_SOURCE:
            raise ValueError("Generated stock movement source is not supported.")
        if movement["movement_id"] in movement_ids or movement["operation_id"] in operation_ids:
            raise ValueError("Generated stock movement identifiers are not unique.")
        movement_ids.add(movement["movement_id"])
        operation_ids.add(movement["operation_id"])
        if movement["stock_after"] - movement["stock_before"] != movement["delta"]:
            raise ValueError("Generated stock movement arithmetic is inconsistent.")
        if movement["stock_before"] != expected_stock[sku] or movement["stock_after"] < 0:
            raise ValueError("Generated stock movement chain is inconsistent.")
        expected_stock[sku] = movement["stock_after"]

    if any(products_by_sku[sku]["stock"] != stock for sku, stock in expected_stock.items()):
        raise ValueError("Generated product stock does not match order and movement arithmetic.")
    if dataset.manifest["sha256"] != _fingerprint({
        "dataset_id": dataset.dataset_id,
        "seed": dataset.seed,
        "products": list(dataset.products),
        "orders": list(dataset.orders),
        "stock_movements": list(dataset.stock_movements),
    }):
        raise ValueError("Generated dataset manifest fingerprint does not match.")
