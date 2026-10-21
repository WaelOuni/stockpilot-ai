"""Explicit disposable PostgreSQL B2B migration commands. No application defaults."""

import argparse
import json
import os

import psycopg

from stockpilot.infrastructure.persistence.postgres.tenant_migrations import (
    PostgresMigrationError,
    PostgresTenantMigrations,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("target", "status", "plan", "apply"))
    parser.add_argument("--schema", default="stockpilot_b2b")
    parser.add_argument("--database")
    parser.add_argument("--system-identifier")
    args = parser.parse_args()
    uri = os.environ.get("POSTGRES_B2B_MIGRATION_URI")
    if not uri:
        parser.error("POSTGRES_B2B_MIGRATION_URI is required; no default.")
    try:
        runner = PostgresTenantMigrations(uri, args.schema)
        if args.command == "target":
            with psycopg.connect(uri, connect_timeout=5) as c:
                c.execute("SET TRANSACTION READ ONLY")
                result = runner.target(c)
        elif args.command == "status":
            result = runner.status()
        elif args.command == "plan":
            result = runner.plan()
        else:
            if not args.database or not args.system_identifier:
                parser.error("apply requires --database and --system-identifier")
            runner.apply(
                database=args.database, system_identifier=args.system_identifier
            )
            result = runner.status()
        print(json.dumps(result, default=str))
        return 0
    except (PostgresMigrationError, psycopg.Error, ValueError):
        print("PostgreSQL B2B migration refused/unavailable; details withheld.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
