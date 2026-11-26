"""Explicit empty-target schema bootstrap and read-only checks for synthetic VPS trials."""
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from pymongo import MongoClient
from stockpilot.infrastructure.config.b2b_demo_entrypoint import load_configuration
from stockpilot.infrastructure.config.bff_settings import BffSettings
from stockpilot.infrastructure.persistence.mongodb.b2b_identity import initialize_b2b_indexes
from stockpilot.infrastructure.persistence.mongodb.bff_store import MongoBffStore
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import MigrationRunner
from stockpilot.infrastructure.persistence.postgres.tenant_migrations import PostgresTenantMigrations


def run(action):
    if action not in {"init-empty", "check"}:
        raise ValueError
    config = load_configuration()
    os.environ.update(config)
    BffSettings().validated_policy()
    operator = json.loads(Path("/run/operator.json").read_text())
    name = config["BFF_MONGO_DATABASE"]
    if name != operator["database"]:
        raise ValueError
    uri = f"postgresql://postgres:{operator['pg']}@postgres:55432/stockpilot_test_vps"
    runner = PostgresTenantMigrations(uri)
    with MongoClient("mongo", username="operator", password=operator["mongo"], replicaSet="rs0",
                     tz_aware=True, serverSelectionTimeoutMS=5000) as mongo, psycopg.connect(uri) as pg:
        db = mongo[name]
        migrations = MigrationRunner(db)
        target, identifier = runner.target(pg)
        if action == "init-empty":
            if db.list_collection_names() or pg.execute("SELECT to_regnamespace('stockpilot_b2b')").fetchone()[0]:
                raise ValueError("Nonempty schemas")
            migrations.apply(confirmation=name)
            initialize_b2b_indexes(db)
            MongoBffStore(db).initialize()
            runner.apply(database=target, system_identifier=identifier)
            pg.execute(sql.SQL("CREATE ROLE stockpilot_bff LOGIN PASSWORD {}").format(sql.Literal(operator["app_pg"])))
            pg.execute("GRANT USAGE ON SCHEMA stockpilot_b2b TO stockpilot_bff")
            pg.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA stockpilot_b2b TO stockpilot_bff")
            pg.execute("REVOKE INSERT, UPDATE, DELETE ON stockpilot_b2b.schema_migrations FROM stockpilot_bff")
            pg.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA stockpilot_b2b TO stockpilot_bff")
            pg.execute("GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO stockpilot_bff")
            db.command("createUser", "stockpilot_bff", pwd=operator["app_mongo"], roles=[{"role": "readWrite", "db": name}])
    with MongoClient(config["BFF_MONGO_URI"], tz_aware=True, serverSelectionTimeoutMS=5000) as client:
        MigrationRunner(client[name]).require_version(7)
    uri = config["BFF_ASSISTANT_POSTGRES_URI"]
    with psycopg.connect(uri) as pg:
        runner = PostgresTenantMigrations(uri)
        runner.target(pg)
        runner.require(pg)
    print("Validated Mongo schema 7 and both PostgreSQL migrations with runtime roles.")


if __name__ == "__main__":
    try:
        run(sys.argv[1])
    except Exception as error:
        sys.exit(f"Qualification refused ({type(error).__name__}). Inspect the private target before retry.")
