"""Explicit local synthetic provisioning/check worker. Never an application startup hook."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import psycopg
from bson import ObjectId
from pymongo import MongoClient

from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
    initialize_b2b_indexes,
)
from stockpilot.infrastructure.persistence.mongodb.bff_store import MongoBffStore
from stockpilot.infrastructure.persistence.mongodb.schema_migrations import (
    MigrationRunner,
)
from stockpilot.infrastructure.persistence.postgres.tenant_migrations import (
    PostgresTenantMigrations,
)

ACCOUNTS = (
    ("demo-a-manager", "org-a", "MANAGER"),
    ("demo-a-user", "org-a", "USER"),
    ("demo-b-user", "org-b", "USER"),
    ("demo-a-admin", "org-a", "ORGANIZATION_ADMIN"),
)


def execute(action, config):
    # Fixed internal topology, namespace and per-installation marker. No URI supplied by a request.
    import re

    installation = config["installation"]
    if not re.fullmatch(r"[0-9a-f]{32}", installation):
        raise ValueError("Installation identity required.")
    name = "stockpilot_b2b_test_" + installation
    admin_uri = (
        "mongodb://operator:"
        + config["mongo_password"]
        + "@mongo:27017/?authSource=admin&replicaSet=rs0&directConnection=true"
    )
    pg_uri = (
        "postgresql://ci:"
        + config["pg_password"]
        + "@test-postgres:55432/stockpilot_ci"
    )
    with MongoClient(
        admin_uri, serverSelectionTimeoutMS=5000, socketTimeoutMS=10000, tz_aware=True
    ) as client:
        db = client[name]
        marker = db.operator_installation.find_one({"_id": "canonical"})
        if action == "init":
            if marker or db.list_collection_names():
                raise ValueError(
                    "Nonempty target refused; initialization never resets."
                )
            db.operator_installation.insert_one(
                {
                    "_id": "canonical",
                    "installation": installation,
                    "phase": "initializing",
                }
            )
            MigrationRunner(db).apply(confirmation=name)
            initialize_b2b_indexes(db)
            MongoBffStore(db).initialize()
            now = datetime.now(UTC)
            for org in ("org-a", "org-b"):
                db.b2b_organizations.insert_one(
                    {
                        "organization_id": org,
                        "name": "Organisation " + org[-1].upper(),
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            for index, (user, org, role) in enumerate(ACCOUNTS, 1):
                subject = f"00000000-0000-4000-8000-{index:012d}"
                db.b2b_users.insert_one(
                    {
                        "user_id": user,
                        "external_identity": {
                            "issuer": config["issuer"],
                            "subject": subject,
                        },
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                db.b2b_memberships.insert_one(
                    {
                        "membership_id": "membership-" + user,
                        "user_id": user,
                        "organization_id": org,
                        "role": role,
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                db.b2b_buyers.insert_one(
                    {
                        "association_id": "buyer-" + user,
                        "organization_id": org,
                        "user_id": user,
                        "customer_id": "customer-" + org,
                        "status": "active",
                        "revision": 1,
                    }
                )
            for org, quantity, price in (("org-a", 10, 4900), ("org-b", 2, 9900)):
                db.b2b_products.insert_one(
                    {
                        "_id": ObjectId(),
                        "organization_id": org,
                        "sku": "DRILL-001",
                        "name": "Perceuse Northline 18V 0476 " + org[-1].upper(),
                        "description": "Produit synthétique de démonstration",
                        "category": "drill",
                        "price_minor": price,
                        "currency": "EUR",
                        "status": "active",
                        "attributes": [],
                        "quantity": quantity,
                        "reserved": 0,
                        "revision": 1,
                    }
                )
            runner = PostgresTenantMigrations(pg_uri)
            with psycopg.connect(pg_uri) as c:
                target, identity = runner.target(c)
            runner.apply(database=target, system_identifier=identity)
            with psycopg.connect(pg_uri) as c:
                c.execute(
                    "CREATE TABLE public.operator_installation (installation text PRIMARY KEY)"
                )
                c.execute(
                    "INSERT INTO public.operator_installation VALUES (%s)",
                    (installation,),
                )
                # Password is a SQL literal, never concatenated into SQL or a process argument.
                from psycopg import sql

                c.execute(
                    sql.SQL("CREATE ROLE stockpilot_bff LOGIN PASSWORD {}").format(
                        sql.Literal(config["app_pg_password"])
                    )
                )
                c.execute("GRANT CONNECT ON DATABASE stockpilot_ci TO stockpilot_bff")
                c.execute(
                    "GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO stockpilot_bff"
                )
                c.execute("GRANT USAGE ON SCHEMA stockpilot_b2b TO stockpilot_bff")
                c.execute(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA stockpilot_b2b TO stockpilot_bff"
                )
                c.execute(
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA stockpilot_b2b TO stockpilot_bff"
                )
            # Provision RAG through its existing adapter, without assigning a permanent RAG_MANAGE role.
            from stockpilot.application.services.identity_authorization import (
                IdentityAuthorizationService,
            )
            from stockpilot.application.services.tenant_authorization import (
                TenantAuthorizationService,
            )
            from stockpilot.domain.entities.identity import ExternalIdentity
            from stockpilot.domain.entities.tenant_authorization import TenantContext
            from stockpilot.domain.entities.tenant_knowledge import IngestTenantDocument
            from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import (
                OllamaEmbeddingAdapter,
            )
            from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
                MongoIdentityRepository,
                MongoMembershipRepository,
                MongoOrganizationRepository,
            )
            from stockpilot.infrastructure.persistence.postgres.tenant_knowledge import (
                PgTenantKnowledge,
            )

            class Authentication:
                def authenticate(self, user):
                    row = db.b2b_users.find_one({"user_id": user})
                    return ExternalIdentity(**row["external_identity"]) if row else None

            resolver = IdentityAuthorizationService(
                Authentication(),
                MongoIdentityRepository(db),
                MongoOrganizationRepository(db),
                MongoMembershipRepository(db),
            )

            class Source:
                def current(self, user):
                    row = db.b2b_memberships.find_one({"user_id": user})
                    return TenantContext(
                        resolver.resolve(user, row["organization_id"]),
                        "explicit-local-provisioning",
                    )

            embeddings = OllamaEmbeddingAdapter(
                SimpleNamespace(
                    ollama_base_url=config["ollama"],
                    ollama_embedding_model="nomic-embed-text",
                    ollama_timeout_seconds=60,
                    ollama_max_prompt_bytes=24576,
                )
            )
            repo = PgTenantKnowledge(
                pg_uri,
                TenantAuthorizationService(Source(), None, None),
                embeddings,
                model="nomic-embed-text",
                version="v1",
            )
            for user, code in (("demo-a-manager", "ALPHA"), ("demo-b-user", "BETA")):
                db.b2b_memberships.update_one(
                    {"user_id": user}, {"$set": {"role": "ORGANIZATION_ADMIN"}}
                )
                try:
                    asyncio.run(
                        repo.ingest(
                            user,
                            Source().current(user),
                            IngestTenantDocument(
                                "initial-document",
                                "same-document",
                                "Garantie",
                                f"La garantie synthétique de cette organisation porte le code {code}-ONLY.",
                                "nomic-embed-text",
                                "v1",
                                0,
                            ),
                        )
                    )
                finally:
                    role = "MANAGER" if user == "demo-a-manager" else "USER"
                    db.b2b_memberships.update_one(
                        {"user_id": user}, {"$set": {"role": role}}
                    )
            db.command(
                "createUser",
                "stockpilot_bff",
                pwd=config["app_mongo_password"],
                roles=[{"role": "readWrite", "db": name}],
            )
            db.operator_installation.update_one(
                {"_id": "canonical"}, {"$set": {"phase": "ready"}}
            )
            marker = db.operator_installation.find_one({"_id": "canonical"})
        if marker != {
            "_id": "canonical",
            "installation": installation,
            "phase": "ready",
        }:
            raise ValueError("Incomplete or foreign installation.")
        MigrationRunner(db).require_version(4)
        runner = PostgresTenantMigrations(pg_uri)
        with psycopg.connect(pg_uri) as c:
            runner.require(c)
            if c.execute(
                "SELECT installation FROM public.operator_installation"
            ).fetchall() != [(installation,)]:
                raise ValueError("Foreign PostgreSQL installation.")
            pending_assistant = c.execute(
                "SELECT count(*) FROM stockpilot_b2b.assistant_turns WHERE status='pending'"
            ).fetchone()[0]
        if action not in {"init", "check"}:
            raise ValueError("Unknown operation.")
        return {
            "mongo_migrations": 4,
            "postgres_migrations": 2,
            "identity_records": db.b2b_users.count_documents({}),
            "durable_order_operations": db.b2b_order_operations.count_documents({}),
            "pending_assistant_turns": pending_assistant,
            "pending_stock_operations": db.b2b_stock_operations.count_documents(
                {"status": "pending"}
            ) + db.b2b_product_archives.count_documents({"status": "pending"}),
            "unknown_outcome_requires_original_key": True,
        }


def main():
    try:
        p = Path("/run/stockpilot/operator.json")
        if p.is_symlink() or p.stat().st_mode & 0o007 or p.stat().st_size > 32768:
            raise ValueError
        result = execute(sys.argv[1], json.loads(p.read_text()))
        print(json.dumps(result))
    except Exception as error:  # noqa: BLE001 - never print driver errors containing private DSNs
        print(
            json.dumps(
                {"error": "Local operator worker refused", "type": type(error).__name__}
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
