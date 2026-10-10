"""Explicit, insert-only PostgreSQL demo schema provisioning; never ingest RAG."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from stockpilot.demo_runtime import verify_postgres_schema, postgres_server_identity
from stockpilot.infrastructure.persistence.postgres.checkpointer import open_checkpointer


ROOT = Path(__file__).resolve().parents[1]
DATABASE = "stockpilot_demo"


def existing_server_uri() -> str:
    """Use the existing container's private environment, not persisted config changes."""
    container = json.loads(subprocess.run(
        ["docker", "inspect", "stockpilot-postgres"], capture_output=True,
        text=True, check=True,
    ).stdout)[0]
    environment = dict(value.split("=", 1) for value in container["Config"]["Env"])
    user = environment["POSTGRES_USER"]
    ports = container["NetworkSettings"]["Ports"]["5432/tcp"]
    port = ports[0]["HostPort"]
    uri = make_conninfo(
        host="127.0.0.1", port=port, user=user,
        password=environment["POSTGRES_PASSWORD"], dbname="postgres",
    )
    result = subprocess.run(
        ["docker", "exec", "-i", "stockpilot-postgres", "psql", "-X", "-At",
         "-U", user, "-d", "postgres", "-v", "ON_ERROR_STOP=1"],
        input="SELECT system_identifier FROM pg_control_system();",
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    with psycopg.connect(uri, connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        identity = str(connection.execute(
            "SELECT system_identifier FROM pg_control_system()"
        ).fetchone()[0])
        if identity != result:
            raise ValueError("TCP PostgreSQL is not the existing Docker server.")
    return uri


def demo_uri() -> str:
    return make_conninfo(existing_server_uri(), dbname=DATABASE)


def provision(uri: str) -> dict:
    if conninfo_to_dict(uri).get("dbname") != DATABASE:
        raise ValueError("Provisioning requires exactly stockpilot_demo.")
    if postgres_server_identity(make_conninfo(uri, dbname="postgres")) != postgres_server_identity(existing_server_uri()):
        raise ValueError("Unexpected PostgreSQL provisioning server; details withheld.")
    with psycopg.connect(make_conninfo(uri, dbname="postgres"),
                        autocommit=True, connect_timeout=5) as admin:
        if not admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,),
        ).fetchone():
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
    with psycopg.connect(uri, autocommit=True, connect_timeout=5) as connection:
        if connection.execute("SELECT current_database()").fetchone()[0] != DATABASE:
            raise ValueError("Connected PostgreSQL database is not stockpilot_demo.")
        connection.execute((ROOT / "migrations" / "000_create_knowledge_chunks.sql").read_text())
        exists = connection.execute(
            "SELECT 1 FROM pg_constraint WHERE conrelid = 'public.knowledge_chunks'::regclass "
            "AND conname = 'knowledge_chunks_source_unique'"
        ).fetchone()
        if not exists:
            connection.execute((ROOT / "migrations" / "001_knowledge_chunks_source_unique.sql").read_text())

    async def setup():
        async with open_checkpointer(uri):
            pass

    asyncio.run(setup())
    with psycopg.connect(uri, connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        verify_postgres_schema(connection, require_checkpoint=True, require_knowledge=True)
        count = connection.execute("SELECT count(*) FROM public.knowledge_chunks").fetchone()[0]
    return {"database": DATABASE, "schema_verified": True, "rag_rows": count}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize-stockpilot-demo", action="store_true", required=True)
    parser.parse_args()
    try:
        print(json.dumps(provision(demo_uri())))
        return 0
    except Exception as error:
        print(f"Demo PostgreSQL setup failed ({type(error).__name__}); details withheld.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
