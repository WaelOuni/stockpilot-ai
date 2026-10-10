from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import init_eval_db


ROOT = Path(__file__).resolve().parents[1]


def test_initial_migration_and_unique_migration_have_distinct_roles():
    initial = (ROOT / "migrations" / "000_create_knowledge_chunks.sql").read_text()
    unique = (ROOT / "migrations" / "001_knowledge_chunks_source_unique.sql").read_text()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in initial
    assert "CREATE TABLE IF NOT EXISTS knowledge_chunks" in initial
    assert "VECTOR(768)" in initial
    assert "source TEXT" in initial
    assert "UNIQUE" not in initial
    assert "UNIQUE NULLS NOT DISTINCT (source)" in unique
    assert "CREATE TABLE" not in unique


def test_existing_eval_database_skips_unique_migration():
    columns = "id:bigint:true\ncontent:text:true\nsource:text:false\nembedding:vector(768):true"
    with patch.object(init_eval_db, "psql", side_effect=[
        "", "", "UNIQUE NULLS NOT DISTINCT (source)", columns, "PRIMARY KEY (id)",
    ]) as sql:
        init_eval_db.main()
    assert [call.args[0] for call in sql.call_args_list] == [
        "postgres", "stockpilot_eval", "stockpilot_eval", "stockpilot_eval", "stockpilot_eval",
    ]
    assert "CREATE DATABASE stockpilot_eval" in sql.call_args_list[0].args[1]
    assert "000_create_knowledge_chunks.sql" in (ROOT / "README.md").read_text()


def test_new_eval_database_applies_initial_then_unique_migration():
    columns = "id:bigint:true\ncontent:text:true\nsource:text:false\nembedding:vector(768):true"
    with patch.object(init_eval_db, "psql", side_effect=[
        "", "", "", "", columns, "PRIMARY KEY (id)",
    ]) as sql:
        init_eval_db.main()
    assert "CREATE TABLE" in sql.call_args_list[1].args[1]
    assert "ADD CONSTRAINT knowledge_chunks_source_unique" in sql.call_args_list[3].args[1]


def test_incompatible_schema_fails_without_destructive_repair():
    with patch.object(init_eval_db, "psql", side_effect=[
        "", "", "UNIQUE NULLS NOT DISTINCT (source)", "embedding:vector(1024):true",
    ]):
        with pytest.raises(ValueError, match="schema differs"):
            init_eval_db.main()


def test_incompatible_constraint_is_not_replaced():
    with patch.object(init_eval_db, "psql", side_effect=[
        "", "", "UNIQUE (source)",
    ]):
        with pytest.raises(ValueError, match="constraint differs"):
            init_eval_db.main()
