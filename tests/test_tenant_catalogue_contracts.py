"""Pure contract/CLI checks supplement, rather than replace, real DB tests."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from pymongo.errors import OperationFailure
from test_tenant_authorization import REQUEST
from test_tenant_authorization import env as env  # noqa: PLC0414

from stockpilot.domain.entities.tenant_product import TenantProductQuery
from stockpilot.domain.enums.tenant_authorization import AuthorizationDecision as D
from stockpilot.domain.enums.tenant_authorization import TenantOperation as O
from stockpilot.infrastructure.persistence.mongodb import migrate


@pytest.mark.parametrize(
    "operation",
    [None, "catalogue_read", "stock_read", {}, O.STOCK_ADJUST, O.ORDER_CREATE],
)
def test_scope_decision_cannot_authorize_untyped_or_mutation_operation(env, operation):
    assert env[0].decide_scope(REQUEST, env[1], operation) is D.DENY


@pytest.mark.parametrize("operation", [O.CATALOGUE_READ, O.STOCK_READ])
def test_scope_revalidates_server_context(env, operation):
    assert env[0].decide_scope(REQUEST, env[1], operation) is D.ALLOW
    env[3].auth.active = False
    assert env[0].decide_scope(REQUEST, env[1], operation) is D.DENY


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": True},
        {"limit": 0},
        {"offset": -1},
        {"offset": 10001},
        {"minimum_price_minor": 1},
        {"minimum_price_minor": 10, "maximum_price_minor": 1, "currency": "EUR"},
        {"status": {"$ne": "inactive"}},
        {"currency": "eur"},
        {"minimum_price_minor": 2**63, "currency": "EUR"},
        {"maximum_price_minor": 10**100, "currency": "EUR"},
    ],
)
def test_typed_query_rejects_unsafe_input(changes):
    with pytest.raises(ValueError):
        TenantProductQuery(**changes)


@pytest.mark.parametrize("command", ["status", "plan", "validate", "apply"])
def test_cli_no_configuration_never_connects(monkeypatch, command, capsys):
    monkeypatch.delenv("STOCKPILOT_MIGRATION_URI", raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail("No connection allowed")

    monkeypatch.setattr(migrate, "MongoClient", forbidden)
    assert migrate.main([command, "--database", "stockpilot_b2b_test_" + "a" * 32]) == 1
    assert "refused" in capsys.readouterr().err


@pytest.mark.parametrize(
    "uri,database,confirmation",
    [
        ("mongodb://127.0.0.1:27017", "stockpilot_b2b_test_" + "a" * 32, None),
        ("mongodb://demo.example:27027", "stockpilot_b2b_test_" + "a" * 32, None),
        ("mongodb://127.0.0.1:27027/wrong", "stockpilot_b2b_test_" + "a" * 32, None),
        ("mongodb://127.0.0.1:27027", "stockpilot", "stockpilot"),
        ("mongodb://127.0.0.1:27027", "stockpilot_b2b_test_" + "a" * 32, "wrong"),
    ],
)
def test_cli_dangerous_target_refused_without_connection(
    monkeypatch, uri, database, confirmation
):
    monkeypatch.setenv("STOCKPILOT_MIGRATION_URI", uri)

    def forbidden(*args, **kwargs):
        pytest.fail("No connection allowed")

    monkeypatch.setattr(migrate, "MongoClient", forbidden)
    args = ["apply", "--database", database]
    if confirmation is not None:
        args += ["--confirm-disposable-database", confirmation]
    assert migrate.main(args) == 1


def test_permissions_failure_sanitized(monkeypatch, capsys):
    monkeypatch.setenv("STOCKPILOT_MIGRATION_URI", "mongodb://127.0.0.1:27027")

    def denied(*args, **kwargs):
        raise OperationFailure("password=private", code=13)

    monkeypatch.setattr(migrate, "MongoClient", denied)
    assert (
        migrate.main(["status", "--database", "stockpilot_b2b_test_" + "a" * 32]) == 1
    )
    assert "private" not in capsys.readouterr().err


def test_imports_never_create_clients():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import pymongo; pymongo.MongoClient = lambda *a, **k: (_ for _ in ()).throw(AssertionError('connection on import')); import stockpilot.infrastructure.persistence.mongodb.schema_migrations; import stockpilot.infrastructure.persistence.mongodb.migrate; import stockpilot.infrastructure.persistence.mongodb.tenant_catalogue",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_no_startup_route_compose_or_legacy_dependency():
    root = Path(__file__).resolve().parents[1]
    for name in ("main.py", "bff.py"):
        tree = ast.parse((root / "src/stockpilot" / name).read_text())
        assert not any(
            isinstance(n, (ast.Import, ast.ImportFrom))
            and any(
                "schema_migrations" in a.name
                or "migrate" in a.name
                or "tenant_catalogue" in a.name
                for a in [*n.names, ast.alias(name=getattr(n, "module", "") or "")]
            )
            for n in ast.walk(tree)
        )
    text = (
        root / "src/stockpilot/infrastructure/persistence/mongodb/tenant_catalogue.py"
    ).read_text()
    assert "product_repository" not in text and "find({})" not in text
    for compose in (root / "docker").glob("*compose*.yml"):
        assert "mongodb.migrate" not in compose.read_text()
