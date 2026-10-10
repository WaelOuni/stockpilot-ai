"""Read-only SKU preflight by default; index creation requires explicit opt-in."""

import argparse
import sys

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.uri_parser import parse_uri

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.product_indexes import (
    preflight_skus, prepare_unique_sku_index, require_unique_sku_index,
)
from stockpilot.application.use_cases.manage_products import ProductSkuIndexNotReadyError


def validate_target(uri: str, database: str, confirmation: str | None, apply: bool) -> None:
    if not database or database != database.strip():
        raise ValueError("An explicit database name is required.")
    if parse_uri(uri)["database"] not in (None, database):
        raise ValueError("Mongo URI database does not match the explicit target.")
    if apply and confirmation != database:
        raise ValueError("--apply requires --confirm-database matching --database exactly.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        validate_target(settings.mongo_uri, args.database, args.confirm_database, args.apply)
        with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000) as client:
            database = client[args.database]
            if database.name != args.database:
                raise ValueError("Connected Mongo database does not match the explicit target.")
            invalid, duplicates = preflight_skus(database.products)
            print(f"SKU preflight: invalid={invalid}, duplicates={duplicates}.")
            if invalid or duplicates:
                print("Blocked: resolve data explicitly; no data modified.", file=sys.stderr)
                return 1
            if args.apply:
                prepare_unique_sku_index(database.products)
                print("Exact unique SKU index provisioned.")
            else:
                print("Read-only preflight; no index or data modified.")
                try:
                    require_unique_sku_index(database.products)
                    print("Activation: exact unique SKU index already provisioned.")
                except ProductSkuIndexNotReadyError:
                    print("Activation blocked: exact unique SKU index not provisioned.")
    except ValueError:
        print("SKU preparation refused: invalid target, opt-in or unsafe product data.", file=sys.stderr)
        return 1
    except PyMongoError:
        print("SKU preparation failed; check Mongo connectivity, index conflicts and concurrent duplicates.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
