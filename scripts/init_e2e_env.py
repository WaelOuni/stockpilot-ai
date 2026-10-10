"""Reset only the dedicated local E2E stores, optionally serving FastAPI."""

import argparse
import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import signal
import sys
from urllib.request import urlopen

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri
from pymongo.errors import PyMongoError

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.product_indexes import prepare_unique_sku_index
from stockpilot.infrastructure.persistence.mongodb.product_import import prepare_import_index
from stockpilot.infrastructure.persistence.mongodb.stock_movement_indexes import (
    prepare_stock_movement_indexes,
)
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer


ROOT = Path(__file__).resolve().parents[1]
DATABASE = "stockpilot_e2e"
INITIAL_STOCK = 10
LOCK_KEY = 724018206
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def validate_targets(mongo_uri: str, mongo_database: str, postgres_uri: str) -> None:
    if mongo_database != DATABASE:
        raise ValueError("E2E Mongo database must be exactly stockpilot_e2e.")
    parsed = parse_uri(mongo_uri)
    if parsed["database"] not in (None, DATABASE):
        raise ValueError("E2E Mongo URI must not name a non-E2E database.")
    if conninfo_to_dict(postgres_uri).get("dbname") != DATABASE:
        raise ValueError("E2E PostgreSQL database must be exactly stockpilot_e2e.")


def configure_environment() -> Settings:
    defaults = Settings()
    mongo_uri = os.environ.get("E2E_MONGO_URI", defaults.mongo_uri)
    postgres_uri = os.environ.get(
        "E2E_POSTGRES_URL", make_conninfo(defaults.postgres_url, dbname=DATABASE),
    )
    validate_targets(mongo_uri, DATABASE, postgres_uri)
    os.environ.update({
        "MONGO_URI": mongo_uri,
        "MONGO_DATABASE": DATABASE,
        "POSTGRES_URL": postgres_uri,
        "LANGGRAPH_CHECKPOINT_DB_URI": postgres_uri,
        "OTEL_ENABLED": "false",
        "LOG_FILE": str(ROOT / "frontend" / "test-results" / "backend.jsonl"),
    })
    return Settings()


def validate_active_settings(settings: Settings) -> None:
    validate_targets(settings.mongo_uri, settings.mongo_database, settings.postgres_url)
    if settings.langgraph_checkpoint_db_uri != settings.postgres_url:
        raise ValueError("E2E checkpoints must use the same dedicated PostgreSQL database.")


async def setup_checkpoints(uri: str) -> None:
    async with open_checkpointer(uri):
        pass


@contextmanager
def locked_database(uri: str):
    if conninfo_to_dict(uri).get("dbname") != DATABASE:
        raise ValueError("Refusing to initialize a non-E2E PostgreSQL database.")
    # Only CREATE DATABASE is issued on the administrative database, never a reset.
    with psycopg.connect(make_conninfo(uri, dbname="postgres"), autocommit=True, connect_timeout=5) as admin:
        if not admin.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            raise RuntimeError("Another E2E run/reset is active; refusing concurrent initialization.")
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)).fetchone():
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
        with psycopg.connect(uri, autocommit=True, connect_timeout=5) as connection:
            if connection.execute("SELECT current_database()").fetchone()[0] != DATABASE:
                raise ValueError("Connected PostgreSQL database is not stockpilot_e2e.")
            yield connection


def reset_stores(settings: Settings, connection, mongo) -> None:
    validate_active_settings(settings)
    if mongo.name != DATABASE or connection.execute("SELECT current_database()").fetchone()[0] != DATABASE:
        raise ValueError("Refusing reset outside stockpilot_e2e.")
    hello = mongo.client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        raise RuntimeError("E2E Mongo requires a writable replica-set primary.")
    asyncio.run(setup_checkpoints(settings.postgres_url))
    with connection.transaction():
        connection.execute(sql.SQL("TRUNCATE {}").format(
            sql.SQL(", ").join(sql.Identifier("public", table) for table in CHECKPOINT_TABLES),
        ))
    # Named collections only: no drop_database, wildcard cleanup or CASCADE.
    MongoOrderRepository(mongo).initialize()
    mongo.orders.delete_many({})
    mongo.products.delete_many({})
    mongo["stock_movements"].delete_many({})
    mongo["product_imports"].delete_many({})
    mongo.products.insert_one({
        "sku": "E2E-BOSCH-X100",
        "name": "Bosch X100",
        "category": "DRILL",
        "price": 99.0,
        "stock": INITIAL_STOCK,
    })
    mongo.products.insert_many([
        {"sku": "E2E-MAKITA-D200", "name": "Makita D200", "category": "DRILL", "price": 169.99, "stock": 0},
        {"sku": "E2E-BOSCH-S500", "name": "Bosch S500", "category": "SAW", "price": 199.99, "stock": 5},
    ])
    # Explicitly authorized E2E provisioning only, after both active-store guards.
    prepare_unique_sku_index(mongo.products)
    prepare_stock_movement_indexes(mongo["stock_movements"])
    prepare_import_index(mongo["product_imports"])
    print("E2E reset: Mongo stockpilot_e2e, PostgreSQL stockpilot_e2e, Bosch X100 stock=10.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Hold the isolation lock while serving FastAPI on 8002.")
    args = parser.parse_args()
    if args.serve:
        def stop(_signum, _frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
    try:
        settings = configure_environment()
        validate_active_settings(settings)
        if args.serve:
            if settings.customer_jwt_secret is None:
                raise ValueError("The Playwright launcher must provide an ephemeral JWT secret.")
            # Fail before reset if the real AI service is unreachable.
            with urlopen(settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError("Ollama is not ready.")
        with locked_database(settings.postgres_url) as connection:
            with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
                reset_stores(settings, connection, client[DATABASE])
                if args.serve:
                    process = subprocess.Popen([
                        sys.executable, "-m", "uvicorn", "stockpilot.main:app",
                        "--host", "127.0.0.1", "--port", "8002",
                    ], cwd=ROOT, env={
                        key: value for key, value in os.environ.items()
                        if key not in {"E2E_ADMIN_PASSWORD", "E2E_ADMIN_USERNAME"}
                    })
                    try:
                        return process.wait()
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=15)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except (psycopg.Error, PyMongoError, OSError) as error:
        print(f"E2E infrastructure failure ({type(error).__name__}); check Mongo/PostgreSQL/Ollama configuration.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
