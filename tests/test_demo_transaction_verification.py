from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from scripts.verify_demo_transactions import CommandAudit, _fixture_plan, main
from stockpilot.demo_dataset import generate_demo_dataset


def test_transaction_fixture_plan_uses_scoped_products_and_uuid4_operations():
    run_id = str(uuid4())
    dataset = generate_demo_dataset()
    manifest, products = _fixture_plan(run_id, 12, dataset)

    assert manifest["demo_test_run_id"] == run_id
    assert manifest["baseline_dataset_id"] == dataset.dataset_id
    assert manifest["baseline_sha256"] == dataset.manifest["sha256"]
    assert len(products) == 13
    assert all(
        product["sku"].startswith(f"DEMO-TEST-{run_id.replace('-', '')}-")
        for product in products
    )
    assert all(UUID(identity).version == 4 for identity in manifest["operation_ids"])
    assert all(UUID(identity).version == 4 for identity in manifest["order_ids"])


def test_non_demo_transaction_cli_refuses_before_connecting(monkeypatch, capsys):
    client = MagicMock()
    monkeypatch.setattr("scripts.verify_demo_transactions.MongoClient", client)

    assert main(["--database", "stockpilot", "--execute"]) == 1
    client.assert_not_called()
    assert "only permitted demo target" in capsys.readouterr().out


def test_transaction_command_audit_flags_writes_outside_approved_demo_collections():
    audit = CommandAudit()
    event = SimpleNamespace(
        command_name="delete",
        command={"delete": "orders"},
        database_name="stockpilot",
    )

    audit.started(event)

    assert audit.unapproved_writes == [("stockpilot", "orders")]
    assert audit.writes["delete:orders"] == 1
