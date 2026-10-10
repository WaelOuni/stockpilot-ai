"""Generate offline or explicitly initialize the isolated synthetic demo dataset."""

import argparse
import sys

from pymongo.errors import PyMongoError
from pymongo import MongoClient

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.demo_dataset import (
    DEFAULT_SEED,
    PLANNED_INDEXES,
    generate_demo_dataset,
)
from stockpilot.demo_persistence import (
    demo_content_summary,
    guarded_database,
    initialize_demo,
    validate_demo_target,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate in memory; no database connection or writes.",
    )
    mode.add_argument(
        "--initialize", action="store_true",
        help="Authorized insert-only initialization of stockpilot_demo; never overwrite.",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings()
        validate_demo_target(settings.mongo_uri, args.database)
        dataset = generate_demo_dataset(args.seed)
    except (ValueError, PyMongoError) as error:
        print(f"Demo preparation refused: {error}", file=sys.stderr)
        return 1

    if args.initialize:
        try:
            with MongoClient(
                settings.mongo_uri, serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000, socketTimeoutMS=15000, appname="stockpilot-demo-initializer",
            ) as client:
                database = guarded_database(client, args.database)
                outcome = initialize_demo(client, database, dataset)
                contents = demo_content_summary(database, dataset)
        except ValueError as error:
            print(f"Demo initialization refused: {error}", file=sys.stderr)
            return 1
        except PyMongoError:
            print(
                "Demo initialization failed (MongoDB details withheld). Do not clean up; "
                "retry the identical seed to verify an ambiguous commit.",
                file=sys.stderr,
            )
            return 1
        print(f"Mongo initialization: {outcome}; complete persisted contents verified.")
        print(f"Mongo target: {args.database} (connected writable rs0 primary verified).")
    else:
        print("Dry run only: no MongoDB or PostgreSQL connection opened; no writes performed.")
        print(f"Mongo target: {args.database} (URI declares replicaSet=rs0; primary not verified).")
    print(f"Dataset: {dataset.dataset_id}")
    print(f"Seed: {dataset.seed}")
    print(f"SHA-256: {dataset.manifest['sha256']}")
    print("Reference dataset counts:" if args.initialize else "Planned reference dataset counts:")
    print(f"  products: {len(dataset.products)}")
    print(f"  orders: {len(dataset.orders)}")
    print(f"  stock_movements: {len(dataset.stock_movements)}")
    print("  demo_manifests: 1")
    if args.initialize:
        print("Preserved isolated test fixture counts:")
        for name, count in contents["test_fixtures"].items():
            print(f"  {name}: {count}")
        print("Total persisted document counts:")
        for name, count in contents["total_documents"].items():
            print(f"  {name}: {count}")
    print("Planned custom indexes (in addition to MongoDB _id_ indexes):")
    for name, keys, unique, partial_filter in PLANNED_INDEXES:
        print(
            f"  {name}: keys={list(keys)}, unique={unique}, "
            f"partial={partial_filter is not None}"
        )
    print("PostgreSQL: not used by dataset preparation; assistant startup not tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
