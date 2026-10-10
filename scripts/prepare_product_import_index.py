"""Dry-run import receipt preflight; --apply requires exact explicit confirmation."""
import argparse
import sys

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.uri_parser import parse_uri
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.persistence.mongodb.product_import import (
    preflight_imports, prepare_import_index, require_import_index,
)
from stockpilot.application.use_cases.product_csv_import import CsvImportIndexNotReady


def validate_target(uri, database, confirmation, apply):
    if not database or database != database.strip():
        raise ValueError("Explicit database required.")
    if parse_uri(uri)["database"] not in (None, database):
        raise ValueError("URI target mismatch.")
    if apply and confirmation != database:
        raise ValueError("Exact --confirm-database required for --apply.")


def main(argv=None):
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
                raise ValueError()
            collection = database["product_imports"]
            invalid, duplicates = preflight_imports(collection)
            print(f"Import preflight: invalid={invalid}, duplicates={duplicates}.")
            if invalid or duplicates:
                return 1
            if args.apply:
                prepare_import_index(collection)
                print("Import receipt index provisioned.")
            else:
                try:
                    require_import_index(collection)
                    print("Import receipt index ready; read-only.")
                except CsvImportIndexNotReady:
                    print("Import activation blocked; no writes performed.")
    except (ValueError, PyMongoError):
        print("Import preparation refused or unavailable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
