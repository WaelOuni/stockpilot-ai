"""Transactional, explicit PostgreSQL B2B migrations; no startup hook."""

import hashlib
import json
import re
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


class PostgresMigrationError(RuntimeError):
    pass


def schema_name(value):
    if value != "stockpilot_b2b" and not re.fullmatch(
        r"stockpilot_checkpoint_test_[0-9a-f]{32}", value or ""
    ):
        raise ValueError("Dedicated B2B schema required.")
    return value


def table(schema, name):
    return sql.Identifier(schema_name(schema), name)


def registry():
    root = Path(__file__).with_name("migrations")
    names = ("0001_b2b_knowledge.sql", "0002_b2b_assistant.sql")
    return tuple(
        (
            number,
            hashlib.sha256((root / name).read_bytes()).hexdigest(),
            (root / name).read_text(),
        )
        for number, name in enumerate(names, 1)
    )


def structure(connection, schema):
    # pg_get_constraintdef/indexdef qualify names according to search_path.
    # Canonicalize this session setting so startup/status compare the same DDL
    # as apply(), including when the connection URI has no options.
    connection.execute(
        sql.SQL("SET LOCAL search_path TO {}, public").format(
            sql.Identifier(schema_name(schema))
        )
    )
    columns = connection.execute(
        "SELECT c.relname,a.attname,tn.nspname,t.typname,a.atttypmod,a.attnotnull,"
        "pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a "
        "JOIN pg_type t ON t.oid=a.atttypid JOIN pg_namespace tn ON tn.oid=t.typnamespace "
        "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
        "WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,a.attnum",
        (schema,),
    ).fetchall()
    constraints = connection.execute(
        "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) "
        "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY 1,2",
        (schema,),
    ).fetchall()
    indexes = connection.execute(
        "SELECT c.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready "
        "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY 1,2",
        (schema,),
    ).fetchall()
    return hashlib.sha256(
        json.dumps([columns, constraints, indexes], default=str).encode()
    ).hexdigest()


class PostgresTenantMigrations:
    def __init__(self, uri, schema="stockpilot_b2b"):
        self.uri, self.schema = uri, schema_name(schema)

    def _history(self, connection):
        exists = connection.execute(
            "SELECT to_regclass(%s)", (self.schema + ".schema_migrations",)
        ).fetchone()[0]
        if not exists:
            occupied = connection.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema=%s LIMIT 1",
                (self.schema,),
            ).fetchone()
            if occupied:
                raise PostgresMigrationError("Untracked B2B schema refused.")
            return []
        rows = connection.execute(
            sql.SQL(
                "SELECT version,checksum,structure FROM {} ORDER BY version"
            ).format(table(self.schema, "schema_migrations"))
        ).fetchall()
        known = registry()
        if len(rows) > len(known) or any(
            r[:2] != known[i][:2] for i, r in enumerate(rows)
        ):
            raise PostgresMigrationError("B2B migration history mismatch.")
        if rows and rows[-1][2] != structure(connection, self.schema):
            raise PostgresMigrationError("B2B schema drift detected.")
        return rows

    def status(self):
        try:
            with psycopg.connect(self.uri, connect_timeout=5) as c:
                c.execute("SET TRANSACTION READ ONLY")
                return self._history(c)
        except psycopg.Error:
            raise PostgresMigrationError(
                "PostgreSQL migration status unavailable."
            ) from None

    def plan(self):
        done = len(self.status())
        return [(v, h) for v, h, _ in registry()[done:]]

    def require(self, connection):
        if len(self._history(connection)) != len(registry()):
            raise PostgresMigrationError("Explicit B2B migration required.")

    def target(self, connection):
        info = conninfo_to_dict(self.uri)
        name, identity = connection.execute(
            "SELECT current_database(), system_identifier::text FROM pg_control_system()"
        ).fetchone()
        if (
            not re.fullmatch(r"stockpilot_(ci|test)(_[a-z0-9]+)*", name)
            or int(info.get("port", 5432)) == 5432
        ):
            raise PostgresMigrationError(
                "Only explicit disposable PostgreSQL targets allowed."
            )
        names = {r[0] for r in connection.execute("SELECT datname FROM pg_database")}
        if any(
            n in {"stockpilot", "stockpilot_e2e"}
            or n.startswith(("stockpilot_demo", "stockpilot_candidate"))
            for n in names
        ):
            raise PostgresMigrationError("Server hosts protected databases.")
        if connection.execute("SELECT to_regclass('public.demo_instance')").fetchone()[
            0
        ]:
            raise PostgresMigrationError("Protected instance refused.")
        return name, identity

    def apply(self, *, database, system_identifier):
        try:
            with psycopg.connect(self.uri, connect_timeout=5) as c:
                c.execute("SET LOCAL lock_timeout='3s'")
                c.execute("SET LOCAL statement_timeout='15s'")
                if self.target(c) != (database, system_identifier):
                    raise PostgresMigrationError("Explicit target identity mismatch.")
                if not c.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
                    ("stockpilot-b2b-migrations:" + self.schema,),
                ).fetchone()[0]:
                    raise PostgresMigrationError("B2B migration locked.")
                rows = self._history(c)
                if len(rows) == len(registry()):
                    return
                c.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        sql.Identifier(self.schema)
                    )
                )
                c.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
                c.execute(
                    sql.SQL("SET LOCAL search_path TO {}, public").format(
                        sql.Identifier(self.schema)
                    )
                )
                c.execute(
                    sql.SQL(
                        "CREATE TABLE IF NOT EXISTS {} (version integer PRIMARY KEY, checksum text NOT NULL, "
                        "structure text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
                    ).format(table(self.schema, "schema_migrations"))
                )
                for v, h, body in registry()[len(rows) :]:
                    c.execute(body, prepare=False)
                    c.execute(
                        sql.SQL(
                            "INSERT INTO {} (version,checksum,structure) VALUES (%s,%s,%s)"
                        ).format(table(self.schema, "schema_migrations")),
                        (v, h, structure(c, self.schema)),
                    )
        except psycopg.Error:
            # PostgreSQL DDL/history are one transaction, no partial schema accepted.
            raise PostgresMigrationError(
                "B2B migration failed; inspect target before retry."
            ) from None
