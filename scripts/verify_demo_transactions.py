"""Run bounded live transaction checks using only manifested stockpilot_demo fixtures."""

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import secrets
from threading import Barrier, Lock
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from bson import ObjectId
import httpx
from pymongo import MongoClient, monitoring
from pymongo.errors import ConnectionFailure, OperationFailure, PyMongoError

from stockpilot.application.order_transaction_errors import OrderOutcomeUnknownError
from stockpilot.application.use_cases.adjust_product_stock import (
    AdjustProductStockUseCase,
    InsufficientStockError,
    ProductStockNotFoundError,
    StockAdjustmentOutcomeUnknownError,
    StockAdjustmentTransactionFailedError,
)
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.domain.entities.order import Order
from stockpilot.domain.enums.order_status import OrderStatus
from stockpilot.demo_dataset import DEFAULT_SEED, DEMO_DATABASE, generate_demo_dataset
from stockpilot.demo_persistence import (
    demo_content_summary,
    guarded_database,
    validate_demo_target,
    validate_existing_schema,
    verify_content,
)
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.stock_adjustment_transaction import MAX_TRANSACTION_ATTEMPTS
from stockpilot.presentation.api.admin_auth import AdminSessions, password_verifier, router as admin_auth_router
from stockpilot.presentation.api.routes import backoffice_stock


ORIGIN = "https://demo.example.test"
APPROVED_COLLECTIONS = {"products", "orders", "stock_movements", "demo_manifests"}
WRITE_COMMANDS = {
    "insert", "update", "findAndModify", "delete", "drop", "dropDatabase",
    "create", "createIndexes", "collMod", "renameCollection",
}


class CommandAudit(monitoring.CommandListener):
    def __init__(self):
        self.counts = Counter()
        self.writes = Counter()
        self.unapproved_writes = []
        self.lock = Lock()

    def started(self, event):
        with self.lock:
            self.counts[event.command_name] += 1
            if event.command_name in WRITE_COMMANDS:
                collection = event.command.get(
                    event.command_name,
                    event.command.get("renameCollection", ""),
                )
                self.writes[f"{event.command_name}:{collection}"] += 1
                if event.database_name != DEMO_DATABASE or collection not in APPROVED_COLLECTIONS:
                    self.unapproved_writes.append((event.database_name, collection))

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


class InjectedTransactionError(ConnectionFailure):
    def __init__(self, label):
        super().__init__("Synthetic client-side transaction fault.")
        self.label = label

    def has_error_label(self, label):
        return label == self.label


class CollectionProxy:
    def __init__(self, collection, *, fail_movement_insert=False):
        self.collection = collection
        self.fail_movement_insert = fail_movement_insert

    def with_options(self, **kwargs):
        return self

    def insert_one(self, document, **kwargs):
        if self.fail_movement_insert:
            raise OperationFailure("Injected movement insert failure.", code=121)
        return self.collection.insert_one(document, **kwargs)

    def __getattr__(self, name):
        return getattr(self.collection, name)


class ClientProxy:
    def __init__(self, client, *, mode="normal", transient_budget=0):
        self.client = client
        self.mode = mode
        self.transient_budget = [transient_budget]
        self.sessions = []

    def start_session(self, *args, **kwargs):
        session = self.client.start_session(*args, **kwargs)
        record = {"commit_calls": 0}
        self.sessions.append(record)
        original_commit = session.commit_transaction
        original_with_transaction = session.with_transaction

        def commit_transaction():
            record["commit_calls"] += 1
            if self.mode == "committed_unknown":
                original_commit()
                raise InjectedTransactionError("UnknownTransactionCommitResult")
            if self.mode == "aborted_unknown":
                session.abort_transaction()
                raise InjectedTransactionError("UnknownTransactionCommitResult")
            if self.mode == "transient" and self.transient_budget[0]:
                self.transient_budget[0] -= 1
                session.abort_transaction()
                raise InjectedTransactionError("TransientTransactionError")
            return original_commit()

        def with_transaction(callback, **options):
            if self.mode == "committed_unknown":
                session.commit_transaction = original_commit
                try:
                    original_with_transaction(callback, **options)
                finally:
                    session.commit_transaction = commit_transaction
                raise InjectedTransactionError("UnknownTransactionCommitResult")
            if self.mode == "aborted_unknown":
                session.start_transaction(**options)
                try:
                    callback(session)
                finally:
                    if session.in_transaction:
                        session.abort_transaction()
                raise InjectedTransactionError("UnknownTransactionCommitResult")
            return original_with_transaction(callback, **options)

        if self.mode != "normal":
            session.commit_transaction = commit_transaction
            if self.mode in {"committed_unknown", "aborted_unknown"}:
                session.with_transaction = with_transaction
        return session

    def __getattr__(self, name):
        return getattr(self.client, name)


class DatabaseProxy:
    def __init__(self, database, *, client=None, fail_movement_insert=False):
        self.database = database
        self.client = client or database.client
        self.fail_movement_insert = fail_movement_insert

    def __getitem__(self, name):
        collection = self.database[name]
        if name == "stock_movements":
            return CollectionProxy(collection, fail_movement_insert=self.fail_movement_insert)
        return collection

    def __getattr__(self, name):
        return getattr(self.database, name)


class DropCommittedResponseTransport(httpx.AsyncBaseTransport):
    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.drop_next_adjustment_response = False
        self.dropped_responses = 0

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if (
            self.drop_next_adjustment_response
            and request.method == "POST"
            and request.url.path.startswith("/backoffice/products/")
            and request.url.path.endswith("/stock-adjustments")
        ):
            await response.aread()
            await response.aclose()
            self.drop_next_adjustment_response = False
            self.dropped_responses += 1
            raise httpx.ReadTimeout("Injected response loss after the ASGI app completed.")
        return response

    async def aclose(self):
        await self.inner.aclose()


def _new_id():
    return str(uuid4())


def _make_order(*, order_id, operation_id, product_name, quantity):
    return Order(
        order_id=order_id,
        operation_id=operation_id,
        product_name=product_name,
        quantity=quantity,
        status=OrderStatus.CREATED,
        created_at=datetime.now(timezone.utc),
        customer_id="demo-test-customer",
    )


def _execute_order(database, order, run_id):
    result = asyncio.run(MongoOrderTransaction(database).execute(order))
    if result is not None:
        tagged = database["orders"].update_one(
            {"order_id": result.order_id},
            {"$set": {"demo_test_run_id": run_id}},
        )
        if tagged.matched_count != 1:
            raise RuntimeError("Committed demo order could not be marked as run-owned.")
    return result


def _cancel_order(database, order_id):
    return asyncio.run(
        CancelOrderUseCase(MongoOrderRepository(database)).execute(order_id)
    )


def _adjust(database, sku, delta, reason, operation_id):
    return asyncio.run(
        AdjustProductStockUseCase(MongoProductRepository(database)).execute(
            sku, delta, reason, operation_id,
        )
    )


def _tag_movement(database, operation_id, run_id):
    result = database["stock_movements"].update_one(
        {"operation_id": operation_id},
        {"$set": {"demo_test_run_id": run_id}},
    )
    if result.matched_count != 1:
        raise RuntimeError("Committed demo movement could not be marked as run-owned.")


def _fixture_plan(run_id, iterations, dataset):
    labels = (
        "reserve-race", "order-replay", "cancel-once", "adjust-race", "adjust-operation-replay",
        "movement-rollback", "adjust-transient", "adjust-committed-unknown",
        "adjust-aborted-unknown", "adjust-zero", "adjust-post-benchmark",
        "order-committed-unknown", "order-aborted-unknown",
    )
    products = []
    for label in labels:
        sku = f"DEMO-TEST-{run_id.replace('-', '')}-{label.upper()}"
        products.append({
            "_id": ObjectId(),
            "sku": sku,
            "name": f"Demo isolated test {run_id[:8]} {label}",
            "category": "OTHER",
            "price": 1.0,
            "stock": {
                "reserve-race": 1,
                "order-replay": 10,
                "cancel-once": 10,
                "adjust-race": 5,
                "adjust-operation-replay": 10,
                "movement-rollback": 6,
                "adjust-transient": 10,
                "adjust-committed-unknown": 10,
                "adjust-aborted-unknown": 10,
                "adjust-zero": 0,
                "adjust-post-benchmark": iterations + 5,
                "order-committed-unknown": 10,
                "order-aborted-unknown": 10,
            }[label],
            "demo_test_run_id": run_id,
            "demo_test_kind": "product",
        })
    operation_ids = [_new_id() for _ in range(30 + iterations)]
    order_ids = [_new_id() for _ in range(9)]
    manifest = {
        "_id": f"stockpilot-demo-test-run-{run_id.replace('-', '')}",
        "demo_test_run_id": run_id,
        "demo_test_kind": "run_manifest",
        "baseline_dataset_id": dataset.dataset_id,
        "baseline_sha256": dataset.manifest["sha256"],
        "status": "started",
        "started_at": datetime.now(timezone.utc),
        "product_skus": [product["sku"] for product in products],
        "order_ids": order_ids,
        "operation_ids": operation_ids,
    }
    return manifest, products


def _insert_plan(database, manifest, products):
    existing = database["products"].count_documents({"sku": {"$in": manifest["product_skus"]}})
    if existing or database["demo_manifests"].count_documents({"_id": manifest["_id"]}):
        raise ValueError("Generated run identity already exists; refusing to reuse fixtures.")

    def insert(session):
        database["products"].insert_many(products, ordered=True, session=session)
        database["demo_manifests"].insert_one(manifest, session=session)

    with database.client.start_session() as session:
        session.with_transaction(insert)


def _concurrent(functions):
    barrier = Barrier(len(functions))

    def run(function):
        barrier.wait(timeout=15)
        return function()

    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        futures = [pool.submit(run, function) for function in functions]
        return [future.result(timeout=60) for future in futures]


def _build_mutation_app(database, password):
    from stockpilot.application.use_cases.adjust_product_stock import (
        GetStockAdjustmentUseCase, ListStockMovementsUseCase,
    )

    app = FastAPI()
    settings = Settings(
        mongo_database=DEMO_DATABASE,
        admin_username="demo-transaction-verifier",
        admin_password_verifier=password_verifier(password),
        admin_trusted_origin=ORIGIN,
        backoffice_api_token=None,
        customer_jwt_secret=None,
        admin_allow_local_http=False,
    )
    app.state.settings = settings
    app.state.admin_sessions = AdminSessions()
    repository = MongoProductRepository(database)
    app.state.adjust_product_stock_use_case = AdjustProductStockUseCase(repository)
    app.state.get_stock_adjustment_use_case = GetStockAdjustmentUseCase(repository)
    app.state.list_stock_movements_use_case = ListStockMovementsUseCase(repository)
    app.include_router(admin_auth_router)
    app.include_router(backoffice_stock.products_router)
    app.include_router(backoffice_stock.operations_router)

    @app.middleware("http")
    async def constrain_test_runtime(request: Request, call_next):
        if (
            request.url.path != "/backoffice/session/login"
            and not request.url.path.startswith("/backoffice/products/")
            and not (
                request.method == "GET"
                and request.url.path.startswith("/backoffice/stock-adjustments/")
            )
        ):
            return JSONResponse(status_code=405, content={"detail": "Demo test route is not enabled."})
        if request.method not in {"GET", "POST"}:
            return JSONResponse(status_code=405, content={"detail": "Demo test method is not enabled."})
        return await call_next(request)

    return app


def _login(client, password):
    response = client.post(
        "/backoffice/session/login",
        json={"username": "demo-transaction-verifier", "password": password},
        headers={"origin": ORIGIN, "x-admin-csrf": "login"},
    )
    if response.status_code != 200:
        raise RuntimeError("Demo admin session setup failed.")
    return response.json()["csrf_token"]


def _benchmark_adjustments(database, sku, operation_ids, run_id):
    password = secrets.token_urlsafe(32)
    app = _build_mutation_app(database, password)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        csrf = _login(client, password)
        timings = []
        statuses = []
        responses = []
        for operation_id in operation_ids:
            started = time.perf_counter()
            response = client.post(
                f"/backoffice/products/{sku}/stock-adjustments",
                json={
                    "operation_id": operation_id,
                    "delta": 1,
                    "reason": "Bounded isolated demo transaction benchmark",
                },
                headers={"origin": ORIGIN, "x-admin-csrf": csrf},
            )
            timings.append((time.perf_counter() - started) * 1000)
            statuses.append(response.status_code)
            responses.append(response.json() if response.status_code == 200 else {})
            if response.status_code == 200:
                _tag_movement(database, operation_id, run_id)
        ordered = sorted(timings)
        errors = sum(status != 200 for status in statuses)
        return {
            "requests": len(operation_ids),
            "errors": errors,
            "status_codes": dict(sorted(Counter(map(str, statuses)).items())),
            "p50_ms": round(ordered[math.ceil(len(ordered) * 0.50) - 1], 3),
            "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
            "transport": "in-process FastAPI TestClient; real guarded route/use case/Mongo transaction",
        }, responses


def _verify_dropped_http_response(database, sku, operation_id, run_id, expected_stock):
    password = secrets.token_urlsafe(32)
    app = _build_mutation_app(database, password)
    transport = DropCommittedResponseTransport(app)

    async def exercise():
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            login = await client.post(
                "/backoffice/session/login",
                json={"username": "demo-transaction-verifier", "password": password},
                headers={"origin": ORIGIN, "x-admin-csrf": "login"},
            )
            if login.status_code != 200:
                raise RuntimeError("Demo admin session setup failed for response-loss check.")
            csrf = login.json()["csrf_token"]
            transport.drop_next_adjustment_response = True
            try:
                await client.post(
                    f"/backoffice/products/{sku}/stock-adjustments",
                    json={
                        "operation_id": operation_id,
                        "delta": 1,
                        "reason": "Injected HTTP response loss after real commit",
                    },
                    headers={"origin": ORIGIN, "x-admin-csrf": csrf},
                )
            except httpx.ReadTimeout:
                pass
            else:
                raise AssertionError("Injected HTTP response-loss wrapper did not drop the response.")
            lookup = await client.get(f"/backoffice/stock-adjustments/{operation_id}")
            history = await client.get(
                f"/backoffice/products/{sku}/stock-movements?limit=100&offset=0",
            )
            if lookup.status_code != 200 or history.status_code != 200:
                raise AssertionError("Operation lookup or stock-history reconciliation failed.")
            return lookup.json(), history.json()

    lookup, history = asyncio.run(exercise())
    movement_count = database["stock_movements"].count_documents({"operation_id": operation_id})
    product = database["products"].find_one({"sku": sku})
    if (
        transport.dropped_responses != 1
        or lookup["operation_id"] != operation_id
        or sum(item["operation_id"] == operation_id for item in history["items"]) != 1
        or movement_count != 1
        or product["stock"] != expected_stock
    ):
        raise AssertionError("Dropped HTTP response was not reconciled by lookup without a retry.")
    _tag_movement(database, operation_id, run_id)
    return {
        "client_timeout_injected_after_application_response": True,
        "lookup_status": 200,
        "history_matches": True,
        "persisted_movements": movement_count,
        "post_count": 1,
        "automatic_retry": False,
        "reread_stock": product["stock"],
    }


def _run_scenarios(database, manifest, products, iterations, progress):
    run_id = manifest["demo_test_run_id"]

    # Product labels map directly by the fixed scenario ordering in _fixture_plan.
    labels = (
        "reserve-race", "order-replay", "cancel-once", "adjust-race", "adjust-operation-replay",
        "movement-rollback", "adjust-transient", "adjust-committed-unknown",
        "adjust-aborted-unknown", "adjust-zero", "adjust-post-benchmark",
        "order-committed-unknown", "order-aborted-unknown",
    )
    products_by_label = dict(zip(labels, products))
    order_index = 0
    op_index = 0

    def new_order(label, qty=1):
        nonlocal order_index, op_index
        order = _make_order(
            order_id=manifest["order_ids"][order_index],
            operation_id=manifest["operation_ids"][op_index],
            product_name=products_by_label[label]["name"],
            quantity=qty,
        )
        order_index += 1
        op_index += 1
        return order

    def new_operation():
        nonlocal op_index
        operation_id = manifest["operation_ids"][op_index]
        op_index += 1
        return operation_id

    results = {}

    progress["scenario"] = "concurrent_reservation"
    reservation_orders = [new_order("reserve-race") for _ in range(2)]
    reservation_results = _concurrent([
        lambda order=order: _execute_order(database, order, run_id)
        for order in reservation_orders
    ])
    stored_reservations = [result for result in reservation_results if result is not None]
    if len(stored_reservations) != 1:
        raise AssertionError("Concurrent reserve did not admit exactly one order against one unit.")
    reservation_stock = database["products"].find_one(
        {"sku": products_by_label["reserve-race"]["sku"]}
    )["stock"]
    if reservation_stock != 0:
        raise AssertionError("Concurrent reserve produced an unexpected stock balance.")
    results["concurrent_reservation"] = {
        "attempts": 2, "committed_orders": 1, "final_stock": reservation_stock,
        "negative_stock": False,
    }

    progress["scenario"] = "concurrent_duplicate_order_operation"
    replay_first = new_order("order-replay", qty=2)
    replay_second = _make_order(
        order_id=manifest["order_ids"][order_index],
        operation_id=replay_first.operation_id,
        product_name=products_by_label["order-replay"]["name"],
        quantity=2,
    )
    order_index += 1
    replay_orders = [replay_first, replay_second]
    replay_results = _concurrent([
        lambda order=order: _execute_order(database, order, run_id)
        for order in replay_orders
    ])
    if replay_results[0] != replay_results[1]:
        raise AssertionError("Concurrent same-operation order replay returned different results.")
    replay_doc_count = database["orders"].count_documents({
        "operation_id": replay_orders[0].operation_id,
    })
    replay_stock = database["products"].find_one(
        {"sku": products_by_label["order-replay"]["sku"]}
    )["stock"]
    if replay_doc_count != 1 or replay_stock != 8:
        raise AssertionError("Concurrent order replay duplicated a reservation.")
    results["concurrent_duplicate_order_operation"] = {
        "requests": 2, "persisted_orders": replay_doc_count, "final_stock": replay_stock,
    }

    progress["scenario"] = "concurrent_cancellation"
    cancel_order = new_order("cancel-once", qty=2)
    created = _execute_order(database, cancel_order, run_id)
    if created is None:
        raise AssertionError("Cancellation fixture reservation unexpectedly failed.")
    cancelled = _concurrent([
        lambda: _cancel_order(database, cancel_order.order_id),
        lambda: _cancel_order(database, cancel_order.order_id),
    ])
    order_doc = database["orders"].find_one({"order_id": cancel_order.order_id})
    cancel_stock = database["products"].find_one(
        {"sku": products_by_label["cancel-once"]["sku"]}
    )["stock"]
    if (
        any(value.status is not OrderStatus.CANCELLED for value in cancelled)
        or cancel_stock != 10
        or [event["status"] for event in order_doc["events"]] != ["CREATED", "CANCELLED"]
    ):
        raise AssertionError("Concurrent cancellation did not restore stock exactly once.")
    results["concurrent_cancellation"] = {
        "requests": 2, "cancelled_events": 1, "final_stock": cancel_stock,
    }

    progress["scenario"] = "concurrent_order_and_adjustment"
    race_order = new_order("adjust-race", qty=3)
    race_operation = new_operation()
    race_results = _concurrent([
        lambda: _execute_order(database, race_order, run_id),
        lambda: _adjust(
            database, products_by_label["adjust-race"]["sku"], 2,
            "Concurrent order and stock adjustment", race_operation,
        ),
    ])
    _tag_movement(database, race_operation, run_id)
    race_stock = database["products"].find_one(
        {"sku": products_by_label["adjust-race"]["sku"]}
    )["stock"]
    if race_results[0] is None or race_stock != 4:
        raise AssertionError("Concurrent order reservation and adjustment lost a stock update.")
    results["concurrent_order_and_adjustment"] = {
        "order_committed": True, "adjustment_committed": True, "final_stock": race_stock,
    }

    progress["scenario"] = "concurrent_duplicate_adjustment_operation"
    replay_operation = new_operation()
    replay_adjustments = _concurrent([
        lambda: _adjust(
            database, products_by_label["adjust-operation-replay"]["sku"], 3,
            "Concurrent duplicate stock adjustment", replay_operation,
        ),
        lambda: _adjust(
            database, products_by_label["adjust-operation-replay"]["sku"], 3,
            "Concurrent duplicate stock adjustment", replay_operation,
        ),
    ])
    _tag_movement(database, replay_operation, run_id)
    replay_adjust_stock = database["products"].find_one(
        {"sku": products_by_label["adjust-operation-replay"]["sku"]}
    )["stock"]
    replay_adjust_count = database["stock_movements"].count_documents(
        {"operation_id": replay_operation}
    )
    if (
        replay_adjustments[0] != replay_adjustments[1]
        or replay_adjust_stock != 13
        or replay_adjust_count != 1
    ):
        raise AssertionError("Concurrent same-operation adjustment was not idempotent.")
    results["concurrent_duplicate_adjustment_operation"] = {
        "requests": 2, "persisted_movements": replay_adjust_count,
        "final_stock": replay_adjust_stock,
    }

    progress["scenario"] = "movement_insert_failure_rollback"
    rollback_operation = new_operation()
    failing_database = DatabaseProxy(database, fail_movement_insert=True)
    try:
        _adjust(
            failing_database, products_by_label["movement-rollback"]["sku"], 3,
            "Injected movement insert rollback", rollback_operation,
        )
    except StockAdjustmentTransactionFailedError:
        pass
    else:
        raise AssertionError("Injected movement insert failure was not surfaced.")
    rollback_stock = database["products"].find_one(
        {"sku": products_by_label["movement-rollback"]["sku"]}
    )["stock"]
    rollback_movements = database["stock_movements"].count_documents(
        {"operation_id": rollback_operation}
    )
    if rollback_stock != 6 or rollback_movements:
        raise AssertionError("Failed movement insert did not roll back its stock update.")
    results["movement_insert_failure_rollback"] = {
        "injected_failure": True, "stock_unchanged": True, "movement_count": 0,
    }

    progress["scenario"] = "bounded_transient_conflict_retry"
    transient_operation = new_operation()
    transient_client = ClientProxy(database.client, mode="transient", transient_budget=2)
    transient_database = DatabaseProxy(database, client=transient_client)
    transient_movement = _adjust(
        transient_database, products_by_label["adjust-transient"]["sku"], 1,
        "Injected bounded transient transaction conflicts", transient_operation,
    )
    _tag_movement(database, transient_operation, run_id)
    transient_sessions = sum(session["commit_calls"] for session in transient_client.sessions)
    transient_stock = database["products"].find_one(
        {"sku": products_by_label["adjust-transient"]["sku"]}
    )["stock"]
    if (
        transient_sessions != MAX_TRANSACTION_ATTEMPTS
        or transient_movement.stock_after != 11
        or transient_stock != 11
        or database["stock_movements"].count_documents({"operation_id": transient_operation}) != 1
    ):
        raise AssertionError("Transient transaction retry was not bounded and idempotent.")
    results["bounded_transient_conflict_retry"] = {
        "injected_aborts": 2, "transaction_attempts": transient_sessions,
        "configured_max_attempts": MAX_TRANSACTION_ATTEMPTS, "final_stock": transient_stock,
    }

    progress["scenario"] = "adjustment_commit_response_lost"
    commit_adjust_operation = new_operation()
    committed_client = ClientProxy(database.client, mode="committed_unknown")
    committed_database = DatabaseProxy(database, client=committed_client)
    commit_adjustment = _adjust(
        committed_database, products_by_label["adjust-committed-unknown"]["sku"], 2,
        "Commit succeeded; simulated response lost", commit_adjust_operation,
    )
    _tag_movement(database, commit_adjust_operation, run_id)
    committed_count = database["stock_movements"].count_documents(
        {"operation_id": commit_adjust_operation}
    )
    committed_stock = database["products"].find_one(
        {"sku": products_by_label["adjust-committed-unknown"]["sku"]}
    )["stock"]
    if committed_count != 1 or committed_stock != 12 or commit_adjustment.stock_after != 12:
        raise AssertionError("Committed ambiguous adjustment did not reconcile exactly once.")
    results["adjustment_commit_response_lost"] = {
        "real_commit": True, "injected_client_response_loss": True,
        "reconciled_movement_count": committed_count, "final_stock": committed_stock,
    }

    progress["scenario"] = "adjustment_uncommitted_unknown"
    aborted_adjust_operation = new_operation()
    aborted_client = ClientProxy(database.client, mode="aborted_unknown")
    aborted_database = DatabaseProxy(database, client=aborted_client)
    try:
        _adjust(
            aborted_database, products_by_label["adjust-aborted-unknown"]["sku"], 2,
            "Transaction aborted; simulated ambiguous result", aborted_adjust_operation,
        )
    except StockAdjustmentOutcomeUnknownError:
        pass
    else:
        raise AssertionError("Aborted ambiguous adjustment was falsely reported as success.")
    aborted_stock = database["products"].find_one(
        {"sku": products_by_label["adjust-aborted-unknown"]["sku"]}
    )["stock"]
    aborted_count = database["stock_movements"].count_documents(
        {"operation_id": aborted_adjust_operation}
    )
    if aborted_stock != 10 or aborted_count:
        raise AssertionError("Aborted ambiguous adjustment changed stock or movement history.")
    results["adjustment_uncommitted_unknown"] = {
        "injected_before_commit_ack": True, "reported_success": False,
        "stock_unchanged": True, "movement_count": aborted_count,
    }

    progress["scenario"] = "missing_sku_and_zero_stock_guards"
    missing_operation = new_operation()
    try:
        _adjust(database, f"DEMO-TEST-{run_id.replace('-', '')}-MISSING", 1,
                "Missing SKU boundary", missing_operation)
    except ProductStockNotFoundError:
        pass
    else:
        raise AssertionError("Adjustment of a missing SKU unexpectedly succeeded.")
    zero_operation = new_operation()
    try:
        _adjust(database, products_by_label["adjust-zero"]["sku"], -1, "Zero stock boundary", zero_operation)
    except InsufficientStockError:
        pass
    else:
        raise AssertionError("Zero-stock decrement unexpectedly succeeded.")
    if database["products"].find_one({"sku": products_by_label["adjust-zero"]["sku"]})["stock"] != 0:
        raise AssertionError("Zero-stock boundary test changed inventory.")
    results["missing_sku_and_zero_stock_guards"] = {
        "missing_sku": "not found; no write", "zero_stock_decrement": "rejected; stock remains 0",
    }

    progress["scenario"] = "order_commit_response_lost"
    committed_order = new_order("order-committed-unknown", qty=2)
    applied_order_client = ClientProxy(database.client, mode="committed_unknown")
    applied_order = _execute_order(
        DatabaseProxy(database, client=applied_order_client), committed_order, run_id,
    )
    applied_order_count = database["orders"].count_documents({"operation_id": committed_order.operation_id})
    applied_order_stock = database["products"].find_one(
        {"sku": products_by_label["order-committed-unknown"]["sku"]}
    )["stock"]
    if applied_order is None or applied_order_count != 1 or applied_order_stock != 8:
        raise AssertionError("Committed ambiguous order was not reconciled exactly once.")
    results["order_commit_response_lost"] = {
        "real_commit": True, "injected_client_response_loss": True,
        "reconciled_order_count": applied_order_count, "final_stock": applied_order_stock,
    }

    progress["scenario"] = "order_uncommitted_unknown"
    uncommitted_order = new_order("order-aborted-unknown", qty=2)
    uncommitted_order_client = ClientProxy(database.client, mode="aborted_unknown")
    try:
        _execute_order(
            DatabaseProxy(database, client=uncommitted_order_client), uncommitted_order, run_id,
        )
    except OrderOutcomeUnknownError:
        pass
    else:
        raise AssertionError("Aborted ambiguous order was falsely reported as success.")
    uncommitted_order_count = database["orders"].count_documents({
        "operation_id": uncommitted_order.operation_id,
    })
    uncommitted_order_stock = database["products"].find_one(
        {"sku": products_by_label["order-aborted-unknown"]["sku"]}
    )["stock"]
    if uncommitted_order_count or uncommitted_order_stock != 10:
        raise AssertionError("Aborted ambiguous order changed stock or created an order.")
    results["order_uncommitted_unknown"] = {
        "injected_before_commit_ack": True, "reported_success": False,
        "stock_unchanged": True, "order_count": uncommitted_order_count,
    }

    progress["scenario"] = "http_response_loss_reconcile"
    dropped_operation = new_operation()
    results["http_response_loss_reconcile"] = _verify_dropped_http_response(
        database, products_by_label["adjust-post-benchmark"]["sku"],
        dropped_operation, run_id, iterations + 6,
    )

    progress["scenario"] = "post_adjustment_benchmark"
    benchmark_operation_ids = [new_operation() for _ in range(iterations)]
    benchmark, responses = _benchmark_adjustments(
        database, products_by_label["adjust-post-benchmark"]["sku"],
        benchmark_operation_ids, run_id,
    )
    if benchmark["errors"] or len(responses) != iterations:
        raise AssertionError("POST stock-adjustment benchmark returned one or more HTTP errors.")
    results["post_adjustment_benchmark"] = benchmark

    progress["scenario"] = "stable_stock_history"
    history = []
    for offset in (0, 5, 10, 15, 20):
        page = asyncio.run(
            MongoProductRepository(database).list_stock_movements(
                products_by_label["adjust-post-benchmark"]["sku"], 5, offset,
            )
        )
        history.extend(page.items)
        if not page.has_more:
            break
    history_ids = [movement.movement_id for movement in history]
    history_keys = [(movement.occurred_at, movement.movement_id) for movement in history]
    if (
        len(history) != iterations + 1
        or len(set(history_ids)) != iterations + 1
        or history_keys != sorted(history_keys, reverse=True)
    ):
        raise AssertionError("Stock movement pagination was not stable across pages.")
    results["stable_stock_history"] = {
        "movements": len(history), "pages": math.ceil(len(history) / 5), "duplicate_ids": 0,
    }
    return results


def run(database, dataset, iterations, *, execute):
    validate_existing_schema(database, complete=True)
    if verify_content(database, dataset) != "complete":
        raise ValueError("The reference dataset is not complete; no test writes permitted.")
    before = demo_content_summary(database, dataset)
    run_id = str(uuid4())
    manifest, products = _fixture_plan(run_id, iterations, dataset)
    if not execute:
        return {
            "run_id": run_id,
            "mode": "dry-run",
            "baseline_before": before,
            "planned_test_fixtures": {
                "products": len(products),
                "orders": len(manifest["order_ids"]),
                "operations": len(manifest["operation_ids"]),
                "post_benchmark_requests": iterations,
            },
            "writes_performed": 0,
        }

    _insert_plan(database, manifest, products)
    results = {}
    progress = {"scenario": "fixture_setup"}
    status = "failed"
    failure = None
    try:
        results = _run_scenarios(database, manifest, products, iterations, progress)
        status = "completed"
    except Exception as error:
        failure = {
            "scenario": progress["scenario"],
            "type": type(error).__name__,
        }
        if isinstance(error, (AssertionError, ValueError, RuntimeError)):
            failure["reason"] = str(error)[:200]
    finally:
        database["demo_manifests"].update_one(
            {"_id": manifest["_id"], "demo_test_run_id": run_id},
            {"$set": {
                "status": status,
                "completed_at": datetime.now(timezone.utc),
                "failure_type": failure["type"] if failure else None,
                "failure_scenario": failure["scenario"] if failure else None,
                "failure_reason": failure.get("reason") if failure else None,
            }},
        )
        after = demo_content_summary(database, dataset)
        if after["reference"] != before["reference"]:
            raise ValueError("Reference dataset changed during demo transaction testing.")
    return {
        "run_id": run_id,
        "mode": "execute",
        "baseline_before": before,
        "baseline_after": after,
        "transaction_results": results,
        "execution_status": status,
        "failure": failure,
        "writes_preserved": True,
        "cleanup_performed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--iterations", type=int, choices=range(10, 21), default=12)
    parser.add_argument("--report", type=Path, default=Path("data/demo-transaction-report.json"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if (
        args.report.is_absolute() or ".." in args.report.parts
        or args.report.suffix != ".json" or not args.report.parent.is_dir()
    ):
        parser.error("--report must be a relative .json path in an existing project directory.")

    audit = CommandAudit()
    try:
        settings = Settings()
        validate_demo_target(settings.mongo_uri, args.database)
        dataset = generate_demo_dataset(args.seed)
        with MongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=15000,
            appname="stockpilot-demo-transaction-verifier",
            event_listeners=[audit],
        ) as client:
            database = guarded_database(client, args.database)
            report = {
                "measured_at": datetime.now(timezone.utc).isoformat(),
                "database": database.name,
                "dataset_id": dataset.dataset_id,
                "baseline_sha256": dataset.manifest["sha256"],
                "transport_scope": "isolated Mongo adapter and in-process TestClient only; no main app/lifespan/PostgreSQL",
                **run(database, dataset, args.iterations, execute=args.execute),
            }
        if audit.unapproved_writes:
            raise ValueError("Mongo command audit detected a write outside the approved demo collections.")
        schema_change_count = sum(
            count for key, count in audit.writes.items()
            if key.split(":", 1)[0] in {
                "create", "createIndexes", "drop", "dropDatabase", "collMod", "renameCollection",
            }
        )
        if schema_change_count:
            raise ValueError("Mongo command audit detected a collection or index schema change.")
        report["mongo_command_counts"] = dict(sorted(audit.counts.items()))
        report["mongo_write_command_counts"] = dict(sorted(audit.writes.items()))
        report["unapproved_mongo_writes"] = 0
        report["mongo_collections_created_or_indexes_changed"] = schema_change_count
        report["retained_harness_diagnostics"] = [
            {
                "status": "failed; retained in the isolated demo manifest",
                "defect": "The first duplicate-order probe assigned different operation UUIDs.",
                "correction": "Both contenders now use one UUIDv4 operation ID.",
            },
            {
                "status": "failed; retained in the isolated demo manifest",
                "defect": "An injected session proxy was not accepted by PyMongo's native session parameter.",
                "correction": "Fault injection now hooks a real PyMongo ClientSession instance.",
            },
        ]
        report["postgresql_accessed"] = False
        report["e2e_started_or_reset"] = False
        encoded = json.dumps(report, indent=2, default=lambda value: value.isoformat())
        args.report.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return int(report.get("execution_status") == "failed")
    except ValueError as error:
        print(f"Demo transaction verification refused: {error}")
        return 1
    except (AssertionError, PyMongoError, RuntimeError) as error:
        print(
            f"Demo transaction verification failed: {type(error).__name__}; "
            "inspect owned fixtures and rerun integrity verification."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
