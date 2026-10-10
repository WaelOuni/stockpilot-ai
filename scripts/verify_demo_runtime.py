"""Explicit live demo validation, retaining manifested API-created test fixtures."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import jwt
from psycopg.conninfo import make_conninfo
from pymongo import MongoClient

from stockpilot.demo_dataset import DEFAULT_SEED, generate_demo_dataset
from stockpilot.demo_persistence import demo_content_summary, guarded_database
from stockpilot.demo_runtime import (
    preflight_demo, resolve_demo_settings, demo_connection_environment,
)
from stockpilot.presentation.api.admin_auth import password_verifier
from stockpilot.application.use_cases.backoffice_dashboard import GetBackofficeDashboardUseCase
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.presentation.api.schemas.backoffice_dashboard import BackofficeDashboardResponse


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:5175"


def call(client, method, path, status=200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != status:
        raise RuntimeError(f"{method} {path} returned {response.status_code}, expected {status}.")
    return response.json()


def business(client, database, dataset, username, password, secret):
    session = call(client, "POST", "/api/backoffice/session/login",
         headers={"X-Admin-CSRF": "login"},
         json={"username": username, "password": password})
    headers = {"X-Admin-CSRF": session["csrf_token"], "Origin": ORIGIN}
    run_id = str(uuid4())
    sku = f"DEMO-TEST-{run_id.replace('-', '')}-RUNTIME"
    manifest_id = f"stockpilot-demo-test-run-{run_id.replace('-', '')}"
    manifest = {
        "_id": manifest_id, "demo_test_kind": "run_manifest", "demo_test_run_id": run_id,
        "baseline_dataset_id": dataset.dataset_id, "baseline_sha256": dataset.manifest["sha256"],
        "status": "started", "product_skus": [], "order_ids": [], "operation_ids": [],
    }
    database.demo_manifests.insert_one(manifest)
    try:
        created = call(client, "POST", "/api/backoffice/products", status=201,
                       headers=headers, json={"sku": sku, "name": f"Bosch DEMO{run_id[:8]}",
                                              "category": "DRILL", "price": 42.5})
        assert created["stock"] == 0
        # Ownership bookkeeping only; all business fields are written through the real API.
        database.products.update_one({"sku": sku}, {"$set": {
            "demo_test_kind": "product", "demo_test_run_id": run_id,
        }})
        database.demo_manifests.update_one({"_id": manifest_id}, {"$push": {"product_skus": sku}})
        for delta in (5, -1):
            operation = str(uuid4())
            database.demo_manifests.update_one({"_id": manifest_id}, {"$push": {"operation_ids": operation}})
            body = {"operation_id": operation, "delta": delta, "reason": "V7 isolated runtime verification"}
            movement = call(client, "POST", f"/api/backoffice/products/{sku}/stock-adjustments",
                            headers=headers, json=body)
            replay = call(client, "POST", f"/api/backoffice/products/{sku}/stock-adjustments",
                          headers=headers, json=body)
            assert movement == replay
            assert database.stock_movements.count_documents({"operation_id": operation}) == 1
        assert call(client, "GET", f"/api/products/{sku}")["stock"] == 4
        unauthorized = client.post(f"/api/backoffice/products/{sku}/stock-adjustments",
                                   json={"operation_id": str(uuid4()), "delta": 1, "reason": "rejected"})
        assert unauthorized.status_code == 403
        customer = f"demo-runtime-{run_id}"
        token = jwt.encode({"customer_id": customer, "exp": int(time.time()) + 1800},
                           secret, algorithm="HS256")
        auth = {"Authorization": f"Bearer {token}"}
        thread = str(uuid4())
        prepared = call(client, "POST", "/api/assistant", headers=auth,
                        json={"thread_id": thread, "message": f"Commande-moi 1 {created['product_name']}"})
        assert prepared["order_status"] == "PENDING_CONFIRMATION", "Real assistant did not prepare ORDER."
        confirmed = call(client, "POST", "/api/assistant", headers=auth,
                         json={"thread_id": thread, "message": "Oui je confirme"})
        assert confirmed["order_status"] == "CONFIRMED"
        order_id = confirmed["created_order_id"]
        order = database.orders.find_one({"order_id": order_id})
        database.demo_manifests.update_one({"_id": manifest_id}, {"$push": {
            "order_ids": order_id, "operation_ids": order["operation_id"],
        }})
        assert call(client, "GET", f"/api/products/{sku}")["stock"] == 3
        assert call(client, "GET", f"/api/orders/{order_id}", headers=auth)["status"] == "CREATED"
        assert call(client, "GET", "/api/orders", headers=auth)["count"] == 1
        call(client, "PATCH", f"/api/backoffice/orders/{order_id}/status",
             headers=headers, json={"status": "PROCESSING"})
        for _ in range(2):
            cancelled = call(client, "POST", f"/api/customer/orders/{order_id}/cancel", headers=auth)
            assert cancelled["status"] == "CANCELLED"
        assert call(client, "GET", f"/api/products/{sku}")["stock"] == 4
        stored = database.orders.find_one({"order_id": order_id})
        assert sum(event["status"] == "CANCELLED" for event in stored["events"]) == 1
        assert database.orders.count_documents({"customer_id": customer}) == 1
        database.demo_manifests.update_one({"_id": manifest_id}, {"$set": {"status": "completed"}})
        return {"run_id": run_id, "sku": sku, "order_id": order_id, "assistant_order": "passed",
                "adjustment_replay": "passed", "cancel_exactly_once": "passed",
                "retained_products": 1, "retained_orders": 1, "retained_movements": 2}
    except Exception:
        # Reconcile ownership after a failed HTTP response; never retry a mutation blindly.
        products = list(database.products.find({"sku": sku}))
        orders = list(database.orders.find({"product_name": f"Bosch DEMO{run_id[:8]}"}))
        if products:
            database.products.update_one({"sku": sku}, {"$set": {
                "demo_test_kind": "product", "demo_test_run_id": run_id,
            }})
        operations = set(database.demo_manifests.find_one({"_id": manifest_id})["operation_ids"])
        operations.update(order["operation_id"] for order in orders)
        database.demo_manifests.update_one({"_id": manifest_id}, {"$set": {
            "status": "failed", "product_skus": [sku] if products else [],
            "order_ids": [order["order_id"] for order in orders], "operation_ids": sorted(operations),
        }})
        raise


def benchmark(client):
    results = {}
    paths = ["/api/products?limit=20", "/api/products?q=DEMO&category=DRILL&available=true",
             "/api/products?available=false", "/api/backoffice/dashboard",
             "/api/backoffice/orders?limit=20"]
    for path in paths:
        call(client, "GET", path)
        samples = []
        for _ in range(10):
            start = time.perf_counter()
            call(client, "GET", path)
            samples.append((time.perf_counter() - start) * 1000)
        samples.sort()
        results[path] = {"requests": 10, "errors": 0,
                         "p50_ms": round(samples[4], 3), "p95_ms": round(samples[9], 3)}
    return results


def run_checks(environment):
    commands = [
        ([sys.executable, "-m", "pytest", "-q"], ROOT),
        ([sys.executable, "-m", "pytest", "-q", "-m", "integration",
          "tests/test_mongo_transaction_integration.py", "tests/test_mongo_product_import_integration.py"], ROOT),
        (["npm", "test", "--", "--run"], ROOT / "frontend"),
        (["npm", "run", "build"], ROOT / "frontend"),
        (["npm", "run", "lint"], ROOT / "frontend"),
        (["npx", "playwright", "test"], ROOT / "frontend"),
    ]
    environment = environment.copy()
    environment.pop("DEMO_ADMIN_PASSWORD", None)
    environment["E2E_POSTGRES_URL"] = make_conninfo(environment["POSTGRES_URL"], dbname="stockpilot_e2e")
    environment["MONGO_DATABASE"] = "stockpilot_e2e"
    results = []
    for command, cwd in commands:
        command_environment = environment.copy()
        if command[1:3] == ["-m", "pytest"]:
            for key in (
                "POSTGRES_URL", "LANGGRAPH_CHECKPOINT_DB_URI", "ADMIN_USERNAME",
                "ADMIN_PASSWORD_VERIFIER", "DEMO_ADMIN_PASSWORD", "CUSTOMER_JWT_SECRET",
                "STOCKPILOT_DEMO_NONINTERACTIVE", "MONGO_DATABASE",
            ):
                command_environment.pop(key, None)
        print(f"Running {' '.join(command[:5])}", flush=True)
        result = subprocess.run(command, cwd=cwd, env=command_environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # Existing suites do not print credentials. Keep only a concise tail.
        print("\n".join(result.stdout.splitlines()[-18:]), flush=True)
        public_command = ["python", *command[1:]] if command[0] == sys.executable else command
        results.append({"command": " ".join(public_command), "exit_code": result.returncode})
        if result.returncode:
            raise RuntimeError(f"Validation command failed: {' '.join(command)}")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--full-checks", action="store_true")
    parser.add_argument("--read-only", action="store_true",
                        help="Reverify the retained completed runtime fixture without business writes.")
    args = parser.parse_args()
    settings = resolve_demo_settings()
    environment = os.environ.copy()
    environment.update({
        **demo_connection_environment(settings),
        "STOCKPILOT_DEMO_NONINTERACTIVE": "true", "OTEL_ENABLED": "false", "LOG_FILE": "",
    })
    username, password, secret = "demo-runtime-admin", secrets.token_urlsafe(36), secrets.token_urlsafe(48)
    environment.update({"ADMIN_USERNAME": username, "ADMIN_PASSWORD_VERIFIER": password_verifier(password),
                        "CUSTOMER_JWT_SECRET": secret, "DEMO_ADMIN_PASSWORD": password})
    preflight = preflight_demo(settings)
    with httpx.Client(timeout=10) as probe:
        models = probe.get(settings.ollama_base_url.rstrip("/") + "/api/tags").json()["models"]
    available = [model["name"] for model in models]
    if settings.ollama_model not in available:
        raise RuntimeError("Configured Ollama model is not installed; no pull/evaluation attempted.")
    print(f"Demo preflight passed; RAG rows={preflight.rag_document_count}; configured model installed.", flush=True)
    dataset = generate_demo_dataset(DEFAULT_SEED)
    report = {"baseline_before": preflight.mongo_summary, "rag_rows": preflight.rag_document_count}
    process = subprocess.Popen(["bash", "scripts/run-demo.sh"], cwd=ROOT, env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with httpx.Client(base_url=ORIGIN, timeout=180, headers={"Origin": ORIGIN}) as client:
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError("Guarded launcher exited before readiness.")
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(.5)
            else:
                raise RuntimeError("Demo did not become ready.")
            with MongoClient(settings.mongo_uri) as mongo:
                database = guarded_database(mongo, "stockpilot_demo")
                if args.read_only:
                    session = call(client, "POST", "/api/backoffice/session/login",
                                   headers={"X-Admin-CSRF": "login"},
                                   json={"username": username, "password": password})
                    manifest = database.demo_manifests.find_one({
                        "status": "completed", "product_skus": {"$regex": "-RUNTIME$"},
                    })
                    if not manifest:
                        raise RuntimeError("No completed retained runtime fixture is available.")
                    sku, order_id = manifest["product_skus"][0], manifest["order_ids"][0]
                    assert call(client, "GET", f"/api/products/{sku}")["stock"] == 4
                    stored = database.orders.find_one({"order_id": order_id})
                    assert stored["status"] == "CANCELLED"
                    assert [event["status"] for event in stored["events"]] == [
                        "CREATED", "PROCESSING", "CANCELLED",
                    ]
                    movements = list(database.stock_movements.find({"product_sku": sku}))
                    assert sorted(movement["delta"] for movement in movements) == [-1, 5]
                    report["business"] = {"run_id": manifest["demo_test_run_id"], "sku": sku,
                                          "order_id": order_id, "mode": "retained_fixture_read_only",
                                          "assistant_order": "previously_completed",
                                          "adjustment_replay": "previously_completed",
                                          "stock_history_deltas": [-1, 5],
                                          "lifecycle": ["CREATED", "PROCESSING", "CANCELLED"],
                                          "cancel_exactly_once": "persisted_verified"}
                else:
                    report["business"] = business(client, database, dataset, username, password, secret)
                report["benchmark"] = benchmark(client)
                report["baseline_after"] = demo_content_summary(database, dataset)
                dashboard = call(client, "GET", "/api/backoffice/dashboard")
                actual = asyncio.run(GetBackofficeDashboardUseCase(
                    MongoOrderRepository(database), MongoProductRepository(database),
                ).execute())
                expected = BackofficeDashboardResponse.model_validate(actual, from_attributes=True).model_dump(mode="json")
                assert dashboard == expected, "HTTP dashboard differs from actual Mongo totals/history."
                report["dashboard"] = dashboard
                environment["DEMO_EXPECTED_DASHBOARD"] = json.dumps(dashboard)
            browser = subprocess.run(["npx", "playwright", "test", "--config", "playwright.demo.config.ts"],
                                     cwd=ROOT / "frontend", env=environment, capture_output=True, text=True)
            print(browser.stdout, flush=True)
            if browser.returncode:
                print(browser.stderr[-2000:], flush=True)
                raise RuntimeError("Demo browser validation failed.")
            report["browser_exit_code"] = browser.returncode
            (ROOT / "data" / "demo-runtime-report.json").write_text(json.dumps(report, indent=2) + "\n")
        if args.full_checks:
            report["checks"] = run_checks(environment)
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=15)
        for port in (8003, 5175):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError(f"Owned demo service on {port} did not stop.")
    report["owned_servers_stopped"] = True
    (ROOT / "data" / "demo-runtime-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if isinstance(error, (RuntimeError, AssertionError)) else type(error).__name__
        print(f"Live demo verification failed: {message}", file=sys.stderr)
        raise SystemExit(1)
