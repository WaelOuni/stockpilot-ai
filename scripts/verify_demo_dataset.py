"""Read-only demo integrity, real HTTP router/adapters, and bounded latency checks."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import jwt
from pymongo import MongoClient, monitoring
from pymongo.errors import PyMongoError

from stockpilot.demo_dataset import DEFAULT_SEED, DEMO_DATABASE, generate_demo_dataset
from stockpilot.demo_persistence import (
    guarded_database, validate_demo_target, validate_existing_schema,
)
from stockpilot.demo_workspace import workspace_summary as demo_content_summary
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.application.use_cases.adjust_product_stock import ListStockMovementsUseCase
from stockpilot.application.use_cases.backoffice_dashboard import GetBackofficeDashboardUseCase
from stockpilot.application.use_cases.backoffice_orders import GetBackofficeOrderUseCase, ListBackofficeOrdersUseCase
from stockpilot.application.use_cases.catalog_products import GetProductUseCase, ListProductsUseCase
from stockpilot.application.use_cases.get_customer_order import GetCustomerOrderUseCase
from stockpilot.application.use_cases.list_customer_orders import ListCustomerOrdersUseCase
from stockpilot.presentation.api.admin_auth import AdminSessions, password_verifier
from stockpilot.presentation.api import admin_auth
from stockpilot.presentation.api.routes import (
    backoffice_dashboard, backoffice_orders, backoffice_stock, orders, products,
)


ORIGIN = "https://demo.example.test"


class ReadCommands(monitoring.CommandListener):
    def __init__(self):
        self.counts = Counter()

    def started(self, event):
        self.counts[event.command_name] += 1

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


def compose_read_app(database, settings: Settings) -> FastAPI:
    """Do not import main, invoke lifespan, initialize repositories, or open PostgreSQL."""
    app = FastAPI()
    app.state.settings = settings
    app.state.admin_sessions = AdminSessions()
    product_repository = MongoProductRepository(database)
    order_repository = MongoOrderRepository(database)
    app.state.list_products_use_case = ListProductsUseCase(product_repository)
    app.state.get_product_use_case = GetProductUseCase(product_repository)
    app.state.list_customer_orders_use_case = ListCustomerOrdersUseCase(order_repository)
    app.state.get_customer_order_use_case = GetCustomerOrderUseCase(order_repository)
    app.state.list_backoffice_orders_use_case = ListBackofficeOrdersUseCase(order_repository)
    app.state.get_backoffice_order_use_case = GetBackofficeOrderUseCase(order_repository)
    app.state.list_stock_movements_use_case = ListStockMovementsUseCase(product_repository)
    app.state.get_backoffice_dashboard_use_case = GetBackofficeDashboardUseCase(
        order_repository, product_repository,
    )
    for router in (
        admin_auth.router, products.router, orders.router, backoffice_orders.router,
        backoffice_dashboard.router, backoffice_stock.products_router,
    ):
        app.include_router(router)

    @app.middleware("http")
    async def forbid_data_mutations(request: Request, call_next):
        if request.method != "GET" and request.url.path not in {
            "/backoffice/session/login", "/backoffice/session/logout",
        }:
            return JSONResponse(status_code=405, content={"detail": "Read-only demo verification."})
        return await call_next(request)

    return app


def _login(client, password):
    response = client.post(
        "/backoffice/session/login",
        json={"username": "demo-verifier", "password": password},
        headers={"origin": ORIGIN, "x-admin-csrf": "login"},
    )
    if response.status_code != 200:
        raise ValueError("Demo verifier session login failed.")


def _expect(response, status=200):
    if response.status_code != status:
        raise ValueError(f"Read-only HTTP validation failed (expected {status}, got {response.status_code}).")
    return response.json()


def _page_ids(client, path, field, *, headers=None, limit=100):
    result = []
    for offset in range(0, 2101, limit):
        separator = "&" if "?" in path else "?"
        body = _expect(client.get(f"{path}{separator}limit={limit}&offset={offset}", headers=headers))
        result.extend(item[field] for item in body["items"])
        if len(body["items"]) < limit:
            break
    if len(set(result)) != len(result):
        raise ValueError("Pagination contains duplicate identities.")
    return result


def _plan_summary(explain):
    stages, indexes = set(), set()

    def walk(value):
        if isinstance(value, dict):
            if "stage" in value:
                stages.add(value["stage"])
            if "indexName" in value:
                indexes.add(value["indexName"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(explain.get("queryPlanner", {}).get("winningPlan", {}))
    stats = explain.get("executionStats", {})
    return {
        "stages": sorted(stages), "indexes": sorted(indexes),
        "returned": stats.get("nReturned"), "docs_examined": stats.get("totalDocsExamined"),
        "keys_examined": stats.get("totalKeysExamined"),
        "server_execution_ms": stats.get("executionTimeMillis"),
    }


def explain_reads(database, sku, customer_id):
    queries = {
        "catalogue": {"find": "products", "filter": {}, "sort": {"sku": 1, "_id": 1}, "limit": 21},
        "catalogue_filtered": {
            "find": "products", "filter": {"category": "DRILL", "stock": {"$gt": 0}},
            "sort": {"sku": 1, "_id": 1}, "limit": 21,
        },
        "product_detail": {"find": "products", "filter": {"sku": sku}, "limit": 2},
        "customer_history": {
            "find": "orders", "filter": {"customer_id": customer_id},
            "sort": {"created_at": -1, "order_id": -1}, "limit": 20,
        },
        "admin_orders": {
            "find": "orders", "filter": {}, "sort": {"created_at": -1, "order_id": -1}, "limit": 20,
        },
        "stock_history": {
            "find": "stock_movements", "filter": {"product_sku": sku},
            "sort": {"occurred_at": -1, "movement_id": -1}, "limit": 21,
        },
        "dashboard_recent_stock": {
            "find": "stock_movements", "filter": {},
            "sort": {"occurred_at": -1, "movement_id": -1}, "limit": 5,
        },
    }
    return {
        name: _plan_summary(database.command("explain", query, verbosity="executionStats"))
        for name, query in queries.items()
    }


def verify_http(database, dataset, iterations):
    password = secrets.token_urlsafe(32)
    customer_secret = secrets.token_urlsafe(48)
    settings = Settings(
        mongo_database=DEMO_DATABASE, admin_username="demo-verifier",
        admin_password_verifier=password_verifier(password), admin_trusted_origin=ORIGIN,
        customer_jwt_secret=customer_secret, backoffice_api_token=None,
        admin_allow_local_http=False,
    )
    app = compose_read_app(database, settings)
    customer_id = dataset.orders[0]["customer_id"]
    customer_headers = {"authorization": "Bearer " + jwt.encode(
        {"customer_id": customer_id, "exp": time.time() + 3600}, customer_secret, algorithm="HS256",
    )}
    sku = dataset.products[0]["sku"]
    order_id = dataset.orders[0]["order_id"]
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        _login(client, password)
        dashboard = _expect(client.get("/backoffice/dashboard"))
        all_products = list(database["products"].find({}))
        all_orders = list(database["orders"].find({}))
        expected_statuses = dict(Counter(order["status"] for order in all_orders))
        expected_products = {
            "total_products": len(all_products),
            "available_products": sum(product["stock"] > 0 for product in all_products),
            "unavailable_products": sum(product["stock"] <= 0 for product in all_products),
            "total_stock_units": sum(product["stock"] for product in all_products),
        }
        if (
            dashboard["order_stats"] != {"total_orders": len(all_orders), "by_status": expected_statuses}
            or dashboard["product_stats"] != expected_products
        ):
            raise ValueError("Dashboard aggregates do not match the persisted fixture.")
        catalogue = _page_ids(client, "/products", "sku", headers=customer_headers)
        if catalogue != [
            product["sku"] for product in sorted(all_products, key=lambda item: (item["sku"], item["_id"]))
        ]:
            raise ValueError("Catalogue pagination does not match persisted reference and owned test products.")
        admin_orders = _page_ids(client, "/backoffice/orders", "order_id")
        def order_sort_key(order):
            created_at = order["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            return created_at, order["order_id"]

        expected_orders = sorted(
            all_orders, key=order_sort_key, reverse=True,
        )
        if admin_orders != [order["order_id"] for order in expected_orders]:
            raise ValueError("Admin pagination does not match persisted reference and owned test orders.")
        customer_orders = _page_ids(
            client, "/orders", "order_id", headers=customer_headers, limit=10,
        )
        if customer_orders != [
            order["order_id"] for order in expected_orders if order.get("customer_id") == customer_id
        ]:
            raise ValueError("Customer ownership/history pagination is inconsistent.")
        stock_history = _page_ids(
            client, f"/backoffice/products/{sku}/stock-movements", "movement_id", limit=2,
        )
        expected_movements = sorted(
            (movement for movement in dataset.stock_movements if movement["product_sku"] == sku),
            key=lambda movement: (movement["occurred_at"], movement["movement_id"]), reverse=True,
        )
        if stock_history != [movement["movement_id"] for movement in expected_movements]:
            raise ValueError("Stock history pagination does not match the persisted fixture.")
        _expect(client.get("/products/DEMO-NOT-FOUND", headers=customer_headers), 404)
        _expect(client.get("/orders/DEMO-NOT-FOUND", headers=customer_headers), 404)
        _expect(client.get(f"/orders/{dataset.orders[1]['order_id']}", headers=customer_headers), 404)
        _expect(client.get("/products?limit=0", headers=customer_headers), 422)
        _expect(client.get("/products?offset=-1", headers=customer_headers), 422)
        if _expect(client.get("/products?offset=10000", headers=customer_headers))["items"]:
            raise ValueError("Out-of-range product pagination should be empty.")
        unavailable = _expect(client.get("/products?available=false", headers=customer_headers))["items"]
        expected_unavailable = sorted(
            product["sku"] for product in all_products if product["stock"] <= 0
        )
        if sorted(item["sku"] for item in unavailable) != expected_unavailable:
            raise ValueError("Unavailable filter does not match persisted reference and test products.")
        _expect(client.post(f"/backoffice/products/{sku}/stock-adjustments", json={}), 405)

        endpoints = {
            "catalogue": ("/products?limit=20", customer_headers),
            "catalogue_filters": ("/products?category=DRILL&available=true&q=Drill&limit=20", customer_headers),
            "catalogue_unavailable_filter": ("/products?available=false", customer_headers),
            "product_detail": (f"/products/{sku}", customer_headers),
            "dashboard": ("/backoffice/dashboard", None),
            "customer_history": ("/orders?limit=20", customer_headers),
            "customer_detail": (f"/orders/{order_id}", customer_headers),
            "admin_orders": ("/backoffice/orders?limit=20", None),
            "admin_order_detail": (f"/backoffice/orders/{order_id}", None),
            "stock_history": (f"/backoffice/products/{sku}/stock-movements?limit=20", None),
        }
        measurements = {}
        for name, (path, headers) in endpoints.items():
            _expect(client.get(path, headers=headers))
            durations, errors = [], 0
            for _ in range(iterations):
                start = time.perf_counter()
                response = client.get(path, headers=headers)
                durations.append((time.perf_counter() - start) * 1000)
                errors += response.status_code != 200
            ordered = sorted(durations)
            measurements[name] = {
                "requests": iterations, "errors": errors,
                "p50_ms": round(ordered[math.ceil(len(ordered) * 0.50) - 1], 3),
                "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
            }
        original_clock = app.state.admin_sessions.clock
        app.state.admin_sessions.clock = lambda: original_clock() + 86401
        _expect(client.get("/backoffice/dashboard"), 401)
        app.state.admin_sessions.clock = original_clock
        _login(client, password)
        _expect(client.get("/backoffice/dashboard"))
        old_cookies = dict(client.cookies)
        # Recomposition replaces the in-memory session store, not the shared Mongo service.
        restarted = compose_read_app(database, settings)
        with TestClient(restarted, base_url=ORIGIN, raise_server_exceptions=False) as restarted_client:
            restarted_client.cookies.update(old_cookies)
            _expect(restarted_client.get("/backoffice/dashboard"), 401)
            _login(restarted_client, password)
            if _expect(restarted_client.get("/backoffice/dashboard")) != dashboard:
                raise ValueError("Read app recomposition changed dashboard contents.")

    return {
        "dashboard": dashboard, "integrity_http": {
            "catalogue_paginated": len(catalogue), "admin_orders_paginated": len(admin_orders),
            "customer_orders_paginated": len(customer_orders), "stock_history_paginated": len(stock_history),
            "missing_and_foreign_details": "404", "pagination_bounds": "422",
            "out_of_range_filter": "empty", "unavailable_filter": "matches persisted stock",
            "data_mutations": "405 blocked",
        },
        "read_latency": measurements,
        "session_resilience": {
            "expired_cookie": "401", "relogin_after_expiry": "200",
            "recomposed_app_old_cookie": "401", "recomposed_app_relogin": "200; dashboard identical",
            "shared_mongo_restart": "not performed", "real_process_restart": "not performed",
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--iterations", type=int, choices=range(10, 31), default=20)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report and (
        args.report.is_absolute() or ".." in args.report.parts
        or args.report.suffix != ".json" or not args.report.parent.is_dir()
    ):
        parser.error("--report must be a relative .json path in an existing project directory.")
    audit = ReadCommands()
    try:
        settings = Settings()
        validate_demo_target(settings.mongo_uri, args.database)
        dataset = generate_demo_dataset(args.seed)
        with MongoClient(
            settings.mongo_uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
            socketTimeoutMS=15000, appname="stockpilot-demo-read-verifier", event_listeners=[audit],
        ) as client:
            database = guarded_database(client, args.database)
            validate_existing_schema(database, complete=True, workspace=True)
            before = demo_content_summary(database, dataset)
            report = {
                "measured_at": datetime.now(timezone.utc).isoformat(),
                "database": database.name, "dataset_id": dataset.dataset_id,
                "sha256": dataset.manifest["sha256"], "seed": dataset.seed,
                "content_before": before,
                "method": "In-process FastAPI TestClient; real routers/use cases/Mongo adapters; sequential reads.",
                "not_measured": [
                    "external HTTP/TLS/browser latency",
                    "CSV import (unapproved receipt collection/index)", "PostgreSQL assistant/checkpointer",
                    "shared service restarts", "real process restarts", "Playwright (reset/write scope withheld)",
                ],
                **verify_http(database, dataset, args.iterations),
                "explain": explain_reads(database, dataset.products[0]["sku"], dataset.orders[0]["customer_id"]),
            }
            after = demo_content_summary(database, dataset)
            if after != before:
                raise ValueError("Dataset changed during read verification.")
            report["content_after"] = after
        forbidden = set(audit.counts) - {
            "hello", "find", "getMore", "aggregate", "explain", "listCollections",
            "listIndexes", "killCursors", "endSessions",
        }
        if forbidden:
            raise ValueError("Read-only command audit contains unexpected Mongo commands.")
        report["mongo_commands"] = dict(sorted(audit.counts.items()))
        report["persistent_write_commands"] = 0
        report["benchmark_requests"] = sum(row["requests"] for row in report["read_latency"].values())
        report["benchmark_errors"] = sum(row["errors"] for row in report["read_latency"].values())
        encoded = json.dumps(report, indent=2)
        if args.report:
            args.report.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return int(report["benchmark_errors"] != 0)
    except ValueError as error:
        print(f"Demo read verification refused: {error}")
        return 1
    except PyMongoError:
        print("Demo read verification failed (MongoDB details withheld); no cleanup attempted.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
