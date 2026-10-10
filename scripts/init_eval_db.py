import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def psql(database, sql):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql",
         "-X", "-v", "ON_ERROR_STOP=1", "-U", "stockpilot", "-d", database, "-At"],
        input=sql, text=True, capture_output=True, check=True, cwd=ROOT,
    ).stdout.strip()


def main():
    psql("postgres", """
SELECT 'CREATE DATABASE stockpilot_eval OWNER stockpilot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'stockpilot_eval')
\\gexec
""")
    initial = (ROOT / "migrations" / "000_create_knowledge_chunks.sql").read_text()
    unique = (ROOT / "migrations" / "001_knowledge_chunks_source_unique.sql").read_text()
    psql("stockpilot_eval", initial)
    constraint = psql("stockpilot_eval", """
SELECT pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid = 'public.knowledge_chunks'::regclass
AND conname = 'knowledge_chunks_source_unique';
""")
    if not constraint:
        psql("stockpilot_eval", unique)
    elif constraint != "UNIQUE NULLS NOT DISTINCT (source)":
        raise ValueError("Existing source constraint differs from migration 001")
    columns = psql("stockpilot_eval", """
SELECT attname || ':' || format_type(atttypid, atttypmod) || ':' || attnotnull
FROM pg_attribute
WHERE attrelid = 'public.knowledge_chunks'::regclass AND attnum > 0 AND NOT attisdropped
ORDER BY attnum;
""")
    if columns.splitlines() != [
        "id:bigint:true", "content:text:true", "source:text:false", "embedding:vector(768):true",
    ]:
        raise ValueError("Existing knowledge_chunks schema differs from migration 000")
    primary = psql("stockpilot_eval", """
SELECT pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid = 'public.knowledge_chunks'::regclass AND contype = 'p';
""")
    if primary != "PRIMARY KEY (id)":
        raise ValueError("Missing or incompatible knowledge_chunks primary key")
    print("stockpilot_eval ready: vector extension, knowledge_chunks VECTOR(768), primary id and unique source")
    print("Application database stockpilot was not modified")


if __name__ == "__main__":
    main()
