"""Read-only stock movement index preflight by default; provision only with explicit confirmation."""

import argparse
import sys

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.uri_parser import parse_uri

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.stock_movement_indexes import (
    has_unique_operation_index,
    prepare_stock_movement_indexes,
    preflight_stock_movements,
)


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
            movements = database["stock_movements"]
            invalid, duplicates = preflight_stock_movements(movements)
            print(f"Stock movement preflight: invalid={invalid}, duplicates={duplicates}.")
            if invalid or duplicates:
                print("Blocked: inspect data explicitly; no data modified.", file=sys.stderr)
                return 1
            if args.apply:
                prepare_stock_movement_indexes(movements)
                print("Stock movement indexes provisioned.")
            elif has_unique_operation_index(movements):
                print("Read-only preflight; operation idempotency index is active.")
            else:
                print("Activation blocked: operation idempotency index is absent.")
    except ValueError:
        print("Stock movement index preparation refused: invalid target, opt-in or data.", file=sys.stderr)
        return 1
    except PyMongoError as error:
        print(
            f"Stock movement index preparation failed ({type(error).__name__}); "
            "check Mongo connectivity and index conflicts.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
