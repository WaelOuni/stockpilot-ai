"""Read-only Mongo checks for the local browser order-flow test."""

import argparse
import os
from datetime import timezone
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.init_e2e_env import DATABASE, INITIAL_STOCK, configure_environment

from pymongo import MongoClient
from bson import ObjectId
from pymongo.errors import PyMongoError

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.product_repository import (
    MongoProductRepository,
)
from stockpilot.infrastructure.persistence.mongodb.product_indexes import require_unique_sku_index
from stockpilot.infrastructure.persistence.mongodb.stock_movement_indexes import require_unique_operation_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stock", "order", "product", "movements", "dashboard"))
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
            if args.action == "dashboard":
                statuses = ("CREATED", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED")
                order_counts = {
                    row["_id"]: row["count"] for row in database.orders.aggregate([
                    {"$match": {"status": {"$in": list(statuses)}}},
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                    ])
                }
                stock = list(database.products.aggregate([{
                    "$group": {
                        "_id": None,
                        "total_products": {"$sum": 1},
                        "available_products": {"$sum": {"$cond": [{"$gt": ["$stock", 0]}, 1, 0]}},
                        "unavailable_products": {"$sum": {"$cond": [{"$lte": ["$stock", 0]}, 1, 0]}},
                        "total_stock_units": {"$sum": "$stock"},
                    },
                }]))
                recent_orders = list(database.orders.find({}, {
                    "_id": 0, "order_id": 1, "product_name": 1, "quantity": 1,
                    "status": 1, "created_at": 1,
                }).sort([("created_at", -1), ("order_id", -1)]).limit(5))
                recent_movements = list(database["stock_movements"].find({}, {
                    "_id": 0, "product_sku": 1, "delta": 1, "occurred_at": 1, "reason": 1,
                }).sort([("occurred_at", -1), ("movement_id", -1)]).limit(5))
                for movement in recent_movements:
                    occurred_at = movement["occurred_at"]
                    if occurred_at.tzinfo is None:
                        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                    movement["occurred_at"] = occurred_at.astimezone(timezone.utc).isoformat()
                print(json.dumps({
                    "order_stats": {
                        "total_orders": database.orders.count_documents({}),
                        "by_status": {status: order_counts.get(status, 0) for status in statuses},
                    },
                    "product_stats": {
                        key: stock[0][key] for key in (
                            "total_products", "available_products", "unavailable_products", "total_stock_units",
                        )
                    } if stock else {
                        "total_products": 0, "available_products": 0,
                        "unavailable_products": 0, "total_stock_units": 0,
                    },
                    "recent_orders": recent_orders,
                    "recent_stock_movements": recent_movements,
                }, default=lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value)))
            elif args.action == "stock":
                products = list(database.products.find(
                    MongoProductRepository._name_filter("Bosch X100"),
                    {"_id": 0, "stock": 1},
                ).limit(2))
                if len(products) != 1:
                    raise ValueError("Expected exactly one Bosch X100 product in Mongo.")
                stock = products[0].get("stock")
                if isinstance(stock, bool) or not isinstance(stock, int) or (stock < 1 if os.environ.get('E2E_NO_RESET') == '1' else stock != INITIAL_STOCK):
                    raise ValueError("Bosch X100 must have the required isolated E2E stock.")
                if database.orders.count_documents({"customer_id": args.customer_id}) != 0:
                    raise ValueError("The generated E2E customer already has orders.")
                print(json.dumps({"stock": stock}))
            elif args.action == "product":
                if not args.order_id:
                    raise ValueError("A product SKU is required.")
                require_unique_sku_index(database.products)
                product = database.products.find_one({"sku": args.order_id})
                if product is None:
                    raise ValueError("E2E product not found.")
                print(json.dumps({
                    "sku": product["sku"], "name": product["name"], "category": product["category"],
                    "price": product["price"], "stock": product["stock"], "identity": str(product["_id"]),
                    "unique_sku_index": True,
                    "customer_orders": database.orders.count_documents({"customer_id": args.customer_id}),
                }))
            elif args.action == "movements":
                if not args.order_id:
                    raise ValueError("A product SKU is required.")
                require_unique_sku_index(database.products)
                require_unique_operation_index(database["stock_movements"])
                product = database.products.find_one({"sku": args.order_id})
                if product is None:
                    raise ValueError("E2E product not found.")
                movements = list(database["stock_movements"].find(
                    {"product_sku": args.order_id},
                    {"_id": 0},
                ).sort([("occurred_at", -1), ("movement_id", -1)]))
                internal_product_matches = all(
                    isinstance(movement.get("product_id"), ObjectId)
                    and movement["product_id"] == product["_id"] for movement in movements
                )
                for movement in movements:
                    movement.pop("product_id", None)
                print(json.dumps({
                    "stock": product["stock"],
                    "movements": movements,
                    "internal_product_matches": internal_product_matches,
                    "customer_orders": database.orders.count_documents({"customer_id": args.customer_id}),
                }, default=lambda value: value.isoformat() if hasattr(value, "isoformat") else str(value)))
            else:
                if not args.order_id:
                    raise ValueError("An order identifier is required.")
                order = database.orders.find_one(
                    {"order_id": args.order_id, "customer_id": args.customer_id},
                    {"_id": 0, "order_id": 1, "product_name": 1, "quantity": 1,
                     "status": 1, "customer_id": 1, "operation_id": 1, "events": 1, "reserved_product_id": 1},
                )
                if order is None:
                    raise ValueError("The created E2E order was not found in the configured Mongo database.")
                product = database.products.find_one({"sku": "E2E-BOSCH-X100"})
                reference = order.pop("reserved_product_id", None)
                print(json.dumps({
                    **order, "remaining_stock": product["stock"],
                    "reserved_product_matches": isinstance(reference, ObjectId) and reference == product["_id"],
                }))
    except PyMongoError:
        print("Mongo E2E read failed; verify MONGO_URI and MONGO_DATABASE.", file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
