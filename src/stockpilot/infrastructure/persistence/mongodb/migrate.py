"""python -m stockpilot.infrastructure.persistence.mongodb.migrate --help"""

import argparse
import json
import os
import sys

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.uri_parser import parse_uri

from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    TARGET,
    MigrationError,
    MigrationRunner,
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explicit B2B Mongo migrations; B1 apply supports disposable databases only."
    )
    parser.add_argument("command", choices=("status", "plan", "validate", "apply"))
    parser.add_argument("--database", required=True)
    parser.add_argument("--confirm-disposable-database")
    args = parser.parse_args(argv)
    try:
        uri = os.environ.get("STOCKPILOT_MIGRATION_URI")
        if not uri or not TARGET.fullmatch(args.database):
            raise MigrationError(
                "Explicit migration URI and disposable database name required; no defaults."
            )
        parsed = parse_uri(uri)
        if parsed["database"] not in (None, args.database) or any(
            host not in ("localhost", "127.0.0.1", "::1") or port == 27017
            for host, port in parsed["nodelist"]
        ):
            raise MigrationError(
                "B1 CLI requires dedicated loopback port and matching database."
            )
        if (
            args.command == "apply"
            and args.confirm_disposable_database != args.database
        ):
            raise MigrationError(
                "Apply requires the exact disposable database confirmation."
            )
        with MongoClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=15000,
            tz_aware=True,
        ) as client:
            runner = MigrationRunner(client[args.database])
            if args.command == "status":
                value = runner.status()
            elif args.command == "plan":
                value = [
                    {
                        "version": m.version,
                        "id": m.migration_id,
                        "description": m.description,
                        "checksum": m.checksum,
                    }
                    for m in runner.plan()
                ]
            elif args.command == "validate":
                runner.validate()
                value = {"valid": True}
            else:
                runner.apply(confirmation=args.confirm_disposable_database)
                value = {"applied": True}
            print(json.dumps(value, default=str))
        return 0
    except (MigrationError, PyMongoError, ValueError):
        # Never render raw URI, credentials, command documents or driver errors.
        print(
            "Mongo migration command refused or failed; inspect sanitized status and recovery guide.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
