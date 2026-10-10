import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from customer_auth import CUSTOMER_JWT_SECRET, customer_headers
from stockpilot.application.use_cases.backoffice_dashboard import GetBackofficeDashboardUseCase
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.main import app, order_repository, product_repository
from stockpilot.presentation.api.admin_auth import AdminSessions, password_verifier


PASSWORD = "test-only-dashboard-admin-password"
ORIGIN = "https://dashboard.example.test"
TOKEN = "test-only-dashboard-technical-token"
STATUS_VALUES = [status.value for status in OrderStatus]


class Cursor:
    def __init__(self, documents, sort_values=None):
        self.documents = deepcopy(documents)
        self.sort_values = deepcopy(sort_values if sort_values is not None else documents)

    def sort(self, fields):
        for key, direction in reversed(fields):
            order = sorted(
                range(len(self.documents)),
                key=lambda index: self.sort_values[index][key],
                reverse=direction < 0,
            )
            self.documents = [self.documents[index] for index in order]
            self.sort_values = [self.sort_values[index] for index in order]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        self.sort_values = self.sort_values[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


class Collection:
    def __init__(self, name, documents=()):
        self.name = name
        self.documents = deepcopy(list(documents))
        self.aggregate_calls = []
        self.find_calls = []
        self.write_calls = []

    def aggregate(self, pipeline):
        self.aggregate_calls.append(deepcopy(pipeline))
        if self.name == "orders":
            facet = pipeline[0]["$facet"]
            statuses = facet["by_status"][0]["$match"]["status"]["$in"]
            counts = Counter(row.get("status") for row in self.documents if row.get("status") in statuses)
            grouped = [{"_id": status, "count": count} for status, count in counts.items()]
            return iter([{
                "total": [{"count": len(self.documents)}] if self.documents else [],
                "by_status": grouped,
            }]) if self.documents else iter([])
        stocks = [row["stock"] for row in self.documents if isinstance(row.get("stock"), (int, float))]
        return iter([{
            "total_products": len(self.documents),
            "available_products": sum(stock > 0 for stock in stocks),
            "unavailable_products": sum(stock <= 0 for stock in stocks),
            "total_stock_units": sum(stocks),
        }]) if self.documents else iter([])

    def find(self, query, projection):
        self.find_calls.append((deepcopy(query), deepcopy(projection)))
        selected = []
        includes = {key for key, value in projection.items() if value == 1}
        sort_values = []
        for row in self.documents:
            if not all(row.get(key) == value for key, value in query.items()):
                continue
            selected.append({key: deepcopy(row[key]) for key in includes if key in row})
            sort_values.append(row)
        return Cursor(selected, sort_values)

    def __getattr__(self, name):
        if name.startswith(("insert", "update", "delete", "replace", "create_index", "drop")):
            def write(*_args, **_kwargs):
                self.write_calls.append(name)
                raise AssertionError(f"Dashboard must not call Mongo {name}.")
            return write
        raise AttributeError(name)


class Database:
    def __init__(self, orders=(), products=(), movements=()):
        self.collections = {
            "orders": Collection("orders", orders),
            "products": Collection("products", products),
            "stock_movements": Collection("stock_movements", movements),
        }

    def __getitem__(self, name):
        return self.collections[name]


def order_document(order_id, status, created_at, *, legacy=False):
    document = {
        "_id": f"mongo-{order_id}",
        "order_id": order_id,
        "operation_id": f"private-operation-{order_id}",
        "product_name": f"Product {order_id}",
        "quantity": 1,
        "status": status,
        "created_at": created_at,
        "reserved_product_id": f"private-reserved-{order_id}",
    }
    if not legacy:
        document["customer_id"] = "customer-1"
    return document


def populated_database():
    base_time = "2026-10-10T10:00:00+00:00"
    orders = [
        order_document(f"order-{status.lower()}", status, base_time)
        for status in STATUS_VALUES
    ]
    orders.append(order_document("legacy-created", "CREATED", base_time, legacy=True))
    orders.append(order_document("unknown-status", "UNEXPECTED", "2020-01-01T00:00:00+00:00"))
    products = [
        {"_id": "private-product-1", "sku": "A", "stock": 10},
        {"_id": "private-product-2", "sku": "B", "stock": 0},
        {"_id": "private-product-3", "sku": "C", "stock": -1},
        {"_id": "private-product-4", "sku": "D", "stock": 2},
    ]
    movements = [
        {
            "_id": f"private-movement-{index}",
            "movement_id": f"{index:02}",
            "operation_id": f"private-operation-{index}",
            "product_id": "private-product-1",
            "product_sku": "A",
            "delta": index + 1,
            "occurred_at": datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc),
            "reason": f"reason-{index}",
        }
        for index in range(7)
    ]
    return Database(orders, products, movements)


def test_mongo_dashboard_uses_aggregates_and_stable_five_item_projections():
    database = populated_database()
    orders = MongoOrderRepository(database)
    products = MongoProductRepository(database)

    order_stats = asyncio.run(orders.get_dashboard_order_stats())
    product_stats = asyncio.run(products.get_dashboard_product_stats())
    recent_orders = asyncio.run(orders.list_recent_dashboard_orders())
    recent_movements = asyncio.run(products.list_recent_dashboard_stock_movements())

    assert order_stats.total_orders == 7
    assert order_stats.by_status == {
        OrderStatus.CREATED: 2,
        OrderStatus.PROCESSING: 1,
        OrderStatus.SHIPPED: 1,
        OrderStatus.DELIVERED: 1,
        OrderStatus.CANCELLED: 1,
    }
    assert product_stats.total_products == 4
    assert product_stats.available_products == 2
    assert product_stats.unavailable_products == 2
    assert product_stats.total_stock_units == 11
    assert len(database["orders"].aggregate_calls) == 1
    facet = database["orders"].aggregate_calls[0][0]["$facet"]
    assert facet["total"] == [{"$count": "count"}]
    assert facet["by_status"][0]["$match"]["status"]["$in"] == STATUS_VALUES
    assert len(recent_orders) == 5
    assert [order.order_id for order in recent_orders] == [
        "order-shipped", "order-processing", "order-delivered", "order-created", "order-cancelled",
    ]
    assert len(recent_movements) == 5
    assert [movement.delta for movement in recent_movements] == [7, 6, 5, 4, 3]
    order_projection = database["orders"].find_calls[0][1]
    movement_projection = database["stock_movements"].find_calls[0][1]
    assert order_projection == {
        "_id": 0, "order_id": 1, "product_name": 1, "quantity": 1, "status": 1, "created_at": 1,
    }
    assert movement_projection == {
        "_id": 0, "product_sku": 1, "delta": 1, "occurred_at": 1, "reason": 1,
    }
    assert all(not collection.write_calls for collection in database.collections.values())


def test_empty_mongo_dashboard_returns_zero_statistics_and_no_recent_rows():
    database = Database()
    use_case = GetBackofficeDashboardUseCase(
        MongoOrderRepository(database), MongoProductRepository(database),
    )

    dashboard = asyncio.run(use_case.execute())

    assert dashboard.order_stats.total_orders == 0
    assert all(count == 0 for count in dashboard.order_stats.by_status.values())
    assert dashboard.product_stats.total_products == 0
    assert dashboard.product_stats.available_products == 0
    assert dashboard.product_stats.unavailable_products == 0
    assert dashboard.product_stats.total_stock_units == 0
    assert dashboard.recent_orders == ()
    assert dashboard.recent_stock_movements == ()
    assert all(not collection.write_calls for collection in database.collections.values())


@pytest.fixture
def dashboard_api(monkeypatch):
    database = populated_database()
    use_case = GetBackofficeDashboardUseCase(
        MongoOrderRepository(database), MongoProductRepository(database),
    )
    settings = Settings(
        admin_username="dashboard-admin",
        admin_password_verifier=password_verifier(PASSWORD),
        admin_trusted_origin=ORIGIN,
        backoffice_api_token=TOKEN,
        customer_jwt_secret=CUSTOMER_JWT_SECRET,
        _env_file=None,
    )
    monkeypatch.setattr(app.state, "settings", settings)
    monkeypatch.setattr(app.state, "admin_sessions", AdminSessions())
    monkeypatch.setattr(app.state, "get_backoffice_dashboard_use_case", use_case)
    return TestClient(app, base_url=ORIGIN, raise_server_exceptions=False), database


def authenticate(client):
    response = client.post(
        "/backoffice/session/login",
        json={"username": "dashboard-admin", "password": PASSWORD},
        headers={"Origin": ORIGIN, "X-Admin-CSRF": "login"},
    )
    assert response.status_code == 200
    return response


def test_session_dashboard_contract_is_minimal_and_not_cached(dashboard_api):
    client, database = dashboard_api
    authenticate(client)

    response = client.get("/backoffice/dashboard")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["order_stats"] == {
        "total_orders": 7,
        "by_status": {
            "CREATED": 2,
            "PROCESSING": 1,
            "SHIPPED": 1,
            "DELIVERED": 1,
            "CANCELLED": 1,
        },
    }
    assert payload["product_stats"] == {
        "total_products": 4,
        "available_products": 2,
        "unavailable_products": 2,
        "total_stock_units": 11,
    }
    assert len(payload["recent_orders"]) == len(payload["recent_stock_movements"]) == 5
    assert set(payload["recent_orders"][0]) == {
        "order_id", "product_name", "quantity", "status", "created_at",
    }
    assert set(payload["recent_stock_movements"][0]) == {
        "product_sku", "delta", "occurred_at", "reason",
    }
    for private in ("reserved_product_id", "private-operation-", "private-reserved-", "product_id", TOKEN):
        assert private not in response.text
    assert all(not collection.write_calls for collection in database.collections.values())


@pytest.mark.parametrize("headers", [
    {},
    customer_headers(),
    {"Authorization": f"Bearer {TOKEN}"},
])
def test_dashboard_rejects_public_customer_and_technical_bearer_before_reads(dashboard_api, headers):
    client, database = dashboard_api

    response = client.get("/backoffice/dashboard", headers=headers)

    assert response.status_code == 401
    assert database["orders"].aggregate_calls == []
    assert database["products"].aggregate_calls == []
    assert database["orders"].find_calls == []
    assert database["stock_movements"].find_calls == []


def test_dashboard_requires_cookie_only_even_when_a_valid_session_is_present(dashboard_api):
    client, database = dashboard_api
    authenticate(client)

    response = client.get("/backoffice/dashboard", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 401
    assert database["orders"].aggregate_calls == []
    assert database["products"].aggregate_calls == []


def test_expired_dashboard_session_is_rejected_before_reads(dashboard_api):
    client, database = dashboard_api
    authenticate(client)
    state = app.state.admin_sessions
    clock = [1000.0]
    state.clock = lambda: clock[0]
    state.sessions[next(iter(state.sessions))] = state.sessions[next(iter(state.sessions))].__class__(
        username="dashboard-admin",
        csrf_token="test-csrf",
        expires_at=clock[0] + 1,
    )
    clock[0] += 1

    response = client.get("/backoffice/dashboard")

    assert response.status_code == 401
    assert database["orders"].aggregate_calls == []
    assert database["products"].aggregate_calls == []


def test_dashboard_persistence_failure_is_not_a_success_shaped_fallback(dashboard_api, monkeypatch):
    client, _ = dashboard_api
    authenticate(client)
    app.state.get_backoffice_dashboard_use_case.execute = AsyncMock(
        side_effect=RuntimeError("private persistence detail"),
    )

    response = client.get("/backoffice/dashboard")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "private persistence detail" not in response.text


def test_dashboard_openapi_is_admin_cookie_only():
    operation = app.openapi()["paths"]["/backoffice/dashboard"]["get"]
    assert operation["security"] == [{"AdminSessionCookie": []}]
