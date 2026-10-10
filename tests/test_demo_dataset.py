import pytest

from stockpilot.demo_dataset import (
    CUSTOMER_COUNT,
    MOVEMENT_COUNT,
    ORDER_COUNT,
    PRODUCT_COUNT,
    generate_demo_dataset,
)
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from scripts.prepare_demo_dataset import main, validate_demo_target


def test_dataset_is_deterministic_and_referentially_consistent():
    first = generate_demo_dataset()
    second = generate_demo_dataset()

    assert first.manifest["sha256"] == second.manifest["sha256"]
    assert first.dataset_id == second.dataset_id
    assert len(first.products) == PRODUCT_COUNT
    assert len(first.orders) == ORDER_COUNT
    assert len(first.stock_movements) == MOVEMENT_COUNT
    assert len({order["customer_id"] for order in first.orders}) == CUSTOMER_COUNT
    assert first.manifest["collections"] == {
        "products": PRODUCT_COUNT,
        "orders": ORDER_COUNT,
        "stock_movements": MOVEMENT_COUNT,
    }
    mapped_orders = [MongoOrderRepository._to_order(order) for order in first.orders]
    assert {order.status.value for order in mapped_orders} == {
        "CREATED", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED",
    }
    assert MongoProductRepository._to_product(first.products[0]).sku == first.products[0]["sku"]
    mapped_movement = MongoProductRepository._to_stock_movement(first.stock_movements[0])
    assert mapped_movement.stock_after - mapped_movement.stock_before == mapped_movement.delta


def test_different_seed_changes_fingerprint():
    assert generate_demo_dataset(17).manifest["sha256"] != generate_demo_dataset(18).manifest["sha256"]


def test_demo_target_requires_exact_database_and_replica_set():
    validate_demo_target("mongodb://localhost:27017/?replicaSet=rs0", "stockpilot_demo")

    with pytest.raises(ValueError, match="only permitted demo target"):
        validate_demo_target("mongodb://localhost:27017/?replicaSet=rs0", "stockpilot")

    with pytest.raises(ValueError, match="URI database"):
        validate_demo_target(
            "mongodb://localhost:27017/stockpilot?replicaSet=rs0", "stockpilot_demo",
        )

    with pytest.raises(ValueError, match="replicaSet"):
        validate_demo_target("mongodb://localhost:27017/", "stockpilot_demo")

    with pytest.raises(ValueError, match="URI is invalid"):
        validate_demo_target("not-a-mongodb-uri", "stockpilot_demo")


def test_dry_run_reports_counts_without_opening_a_database(monkeypatch, capsys):
    class TestSettings:
        mongo_uri = "mongodb://localhost:27017/?replicaSet=rs0"

    monkeypatch.setattr("scripts.prepare_demo_dataset.Settings", TestSettings)

    assert main(["--database", "stockpilot_demo", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "no MongoDB or PostgreSQL connection opened" in output
    assert "products: 500" in output
    assert "orders: 2000" in output
    assert "stock_movements: 3000" in output
    assert "demo_manifests: 1" in output


def test_dry_run_refuses_non_demo_target(monkeypatch, capsys):
    class TestSettings:
        mongo_uri = "mongodb://localhost:27017/?replicaSet=rs0"

    monkeypatch.setattr("scripts.prepare_demo_dataset.Settings", TestSettings)

    assert main(["--database", "stockpilot", "--dry-run"]) == 1
    assert "only permitted demo target" in capsys.readouterr().err
