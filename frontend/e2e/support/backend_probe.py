"""Read-only Mongo checks for the local browser order-flow test."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.init_e2e_env import DATABASE, INITIAL_STOCK, configure_environment

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.product_repository import (
    MongoProductRepository,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stock", "order"))
    parser.add_argument("customer_id")
    parser.add_argument("order_id", nargs="?")
    args = parser.parse_args()
    settings = Settings()
    if settings.mongo_database != DATABASE:
        raise ValueError("The browser E2E probe refuses non-E2E Mongo targets.")
    settings = configure_environment()
    try:
        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            database = client[settings.mongo_database]
            if args.action == "stock":
                products = list(database.products.find(
                    MongoProductRepository._name_filter("Bosch X100"),
                    {"_id": 0, "stock": 1},
                ).limit(2))
                if len(products) != 1:
                    raise ValueError("Expected exactly one Bosch X100 product in Mongo.")
                stock = products[0].get("stock")
                if isinstance(stock, bool) or not isinstance(stock, int) or stock != INITIAL_STOCK:
                    raise ValueError("Bosch X100 must start with exactly 10 E2E stock units.")
                if database.orders.count_documents({"customer_id": args.customer_id}) != 0:
                    raise ValueError("The generated E2E customer already has orders.")
                print(json.dumps({"stock": stock}))
            else:
                if not args.order_id:
                    raise ValueError("An order identifier is required.")
                order = database.orders.find_one(
                    {"order_id": args.order_id, "customer_id": args.customer_id},
                    {"_id": 0, "order_id": 1, "product_name": 1, "quantity": 1,
                     "status": 1, "customer_id": 1, "operation_id": 1, "events": 1},
                )
                if order is None:
                    raise ValueError("The created E2E order was not found in the configured Mongo database.")
                stock = database.products.find_one({"sku": "E2E-BOSCH-X100"})["stock"]
                print(json.dumps({**order, "remaining_stock": stock}))
    except PyMongoError:
        print("Mongo E2E read failed; verify MONGO_URI and MONGO_DATABASE.", file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
