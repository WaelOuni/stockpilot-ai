from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from bson import BSON, ObjectId
from bson.codec_options import CodecOptions

from stockpilot.demo_dataset import generate_demo_dataset
from stockpilot.demo_persistence import (
    COLLECTIONS,
    expected_documents,
    guarded_database,
    initialize_demo,
    demo_content_summary,
    validate_existing_schema,
    verify_content,
)


@pytest.fixture(scope="module")
def dataset():
    return generate_demo_dataset()


def database_with(documents):
    database = MagicMock()
    database.name = "stockpilot_demo"
    collections = {}
    for name in COLLECTIONS:
        collection = MagicMock()
        collection.find.return_value = [
            BSON.encode(document).decode(codec_options=CodecOptions(tz_aware=True))
            for document in documents.get(name, ())
        ]
        collections[name] = collection
    database.__getitem__.side_effect = collections.__getitem__
    return database


@pytest.mark.parametrize("hello", [
    {"setName": "stockpilot", "isWritablePrimary": True},
    {"setName": "rs0", "isWritablePrimary": False},
    {"setName": "rs0", "isWritablePrimary": True},
])
def test_actual_connection_guard_refuses_wrong_primary_or_no_transactions(hello):
    client = MagicMock()
    client.admin.command.return_value = hello
    with pytest.raises(ValueError):
        guarded_database(client, "stockpilot_demo")
    client.get_database.assert_not_called()


def test_guard_refuses_normal_database_without_connecting():
    client = MagicMock()
    with pytest.raises(ValueError):
        guarded_database(client, "stockpilot")
    client.admin.command.assert_not_called()


def test_guard_checks_effective_database():
    client = MagicMock()
    client.admin.command.return_value = {
        "setName": "rs0", "isWritablePrimary": True,
        "logicalSessionTimeoutMinutes": 30, "maxWireVersion": 17,
    }
    client.get_database.return_value = SimpleNamespace(name="stockpilot")
    with pytest.raises(ValueError, match="effective database"):
        guarded_database(client, "stockpilot_demo")


def test_content_verification_requires_exact_full_manifest_and_documents(dataset):
    assert verify_content(database_with({}), dataset) == "empty"
    assert verify_content(database_with(expected_documents(dataset)), dataset) == "complete"
    partial = {"products": dataset.products}
    with pytest.raises(ValueError, match="partial/incompatible"):
        verify_content(database_with(partial), dataset)
    changed = deepcopy(expected_documents(dataset))
    changed["products"][0]["stock"] += 1
    with pytest.raises(ValueError, match="partial/incompatible"):
        verify_content(database_with(changed), dataset)
    changed = deepcopy(expected_documents(dataset))
    changed["demo_manifests"][0]["sha256"] = "wrong"
    with pytest.raises(ValueError, match="partial/incompatible"):
        verify_content(database_with(changed), dataset)


def test_content_verification_separates_manifested_test_documents_from_reference(dataset):
    run_id = str(uuid4())
    product_id = ObjectId()
    sku = f"DEMO-TEST-{run_id.replace('-', '')}-CHECK"
    product = {
        "_id": product_id,
        "sku": sku,
        "name": "Isolated transaction test product",
        "category": "OTHER",
        "price": 1.0,
        "stock": 4,
        "demo_test_run_id": run_id,
        "demo_test_kind": "product",
    }
    manifest = {
        "_id": f"stockpilot-demo-test-run-{run_id.replace('-', '')}",
        "demo_test_run_id": run_id,
        "demo_test_kind": "run_manifest",
        "baseline_dataset_id": dataset.dataset_id,
        "baseline_sha256": dataset.manifest["sha256"],
        "status": "started",
        "product_skus": [sku],
        "order_ids": [],
        "operation_ids": [],
    }
    documents = expected_documents(dataset)
    documents["products"] = (*documents["products"], product)
    documents["demo_manifests"] = (*documents["demo_manifests"], manifest)
    database = database_with(documents)

    assert verify_content(database, dataset) == "complete"
    summary = demo_content_summary(database, dataset)
    assert summary["reference"]["sha256"] == dataset.manifest["sha256"]
    assert summary["reference"]["products"] == 500
    assert summary["test_fixtures"] == {
        "runs": 1, "runs_by_status": {"started": 1},
        "products": 1, "orders": 0, "stock_movements": 0,
    }
    assert summary["total_documents"]["products"] == 501


def test_manifest_operation_ids_recover_test_order_and_movement_without_postcommit_tags(dataset):
    run_id = str(uuid4())
    product_id = ObjectId()
    sku = f"DEMO-TEST-{run_id.replace('-', '')}-RECOVERY"
    order_id, order_operation_id, movement_operation_id = (
        str(uuid4()), str(uuid4()), str(uuid4()),
    )
    product = {
        "_id": product_id, "sku": sku, "name": "Recoverable isolated test product",
        "category": "OTHER", "price": 1.0, "stock": 7,
        "demo_test_run_id": run_id, "demo_test_kind": "product",
    }
    order = {
        "_id": ObjectId(), "order_id": order_id, "operation_id": order_operation_id,
        "product_name": product["name"], "quantity": 1,
        "reserved_product_id": product_id,
    }
    movement = {
        "_id": ObjectId(), "movement_id": str(uuid4()),
        "operation_id": movement_operation_id, "product_sku": sku,
        "product_id": product_id, "delta": 2, "stock_before": 5, "stock_after": 7,
    }
    manifest = {
        "_id": f"stockpilot-demo-test-run-{run_id.replace('-', '')}",
        "demo_test_run_id": run_id, "demo_test_kind": "run_manifest",
        "baseline_dataset_id": dataset.dataset_id,
        "baseline_sha256": dataset.manifest["sha256"], "status": "started",
        "product_skus": [sku], "order_ids": [order_id],
        "operation_ids": [order_operation_id, movement_operation_id],
    }
    documents = expected_documents(dataset)
    documents["products"] = (*documents["products"], product)
    documents["orders"] = (*documents["orders"], order)
    documents["stock_movements"] = (*documents["stock_movements"], movement)
    documents["demo_manifests"] = (*documents["demo_manifests"], manifest)

    summary = demo_content_summary(database_with(documents), dataset)
    assert summary["test_fixtures"]["orders"] == 1
    assert summary["test_fixtures"]["stock_movements"] == 1


def test_content_verification_refuses_unmanifested_test_documents(dataset):
    documents = expected_documents(dataset)
    documents["products"] = (*documents["products"], {
        "_id": ObjectId(), "sku": "DEMO-TEST-UNOWNED", "name": "Unowned",
        "category": "OTHER", "price": 1.0, "stock": 1,
    })
    with pytest.raises(ValueError, match="Unowned or invalid demo test product"):
        verify_content(database_with(documents), dataset)


@pytest.mark.parametrize("info", [
    {"name": "unapproved", "type": "collection"},
    {"name": "products", "type": "view"},
    {"name": "products", "type": "collection", "options": {"capped": True}},
])
def test_schema_refuses_unapproved_collections_or_options(info):
    database = MagicMock()
    database.list_collections.return_value = [info]
    with pytest.raises(ValueError, match="incompatible"):
        validate_existing_schema(database)
    database.create_collection.assert_not_called()


def test_schema_refuses_extra_index():
    database = MagicMock()
    database.list_collections.return_value = [{"name": "products", "type": "collection"}]
    database["products"].list_indexes.return_value = [{
        "name": "unapproved", "key": {"name": 1},
    }]
    with pytest.raises(ValueError, match="indexes are incompatible"):
        validate_existing_schema(database)


def test_incompatible_contents_refuse_before_any_write(dataset, monkeypatch):
    database = database_with({"products": dataset.products[:1]})
    database.list_collections.return_value = []
    monkeypatch.setattr("stockpilot.demo_persistence.guarded_database", lambda *args: database)
    client = MagicMock()
    database.client = client
    with pytest.raises(ValueError, match="partial/incompatible"):
        initialize_demo(client, database, dataset)
    database.create_collection.assert_not_called()
    client.start_session.assert_not_called()
    for name in COLLECTIONS:
        database[name].insert_many.assert_not_called()
        database[name].create_index.assert_not_called()


def test_complete_replay_performs_no_writes(dataset, monkeypatch):
    database = database_with(expected_documents(dataset))
    monkeypatch.setattr("stockpilot.demo_persistence.guarded_database", lambda *args: database)
    monkeypatch.setattr("stockpilot.demo_persistence.validate_existing_schema", lambda *args, **kwargs: None)
    client = MagicMock()
    database.client = client
    assert initialize_demo(client, database, dataset) == "replayed"
    database.create_collection.assert_not_called()
    client.start_session.assert_not_called()
    for name in COLLECTIONS:
        database[name].insert_many.assert_not_called()
        database[name].create_index.assert_not_called()


def test_initialization_uses_only_six_indexes_and_one_transaction(dataset, monkeypatch):
    database = database_with({})
    database.list_collection_names.return_value = []
    inserted = {name: [] for name in COLLECTIONS}
    for name in COLLECTIONS:
        database[name].find.side_effect = lambda *args, _name=name, **kwargs: list(inserted[_name])
        database[name].insert_many.side_effect = (
            lambda documents, *, session, _name=name:
            inserted[_name].extend(
                BSON.encode(document).decode(codec_options=CodecOptions(tz_aware=True))
                for document in documents
            )
        )
    monkeypatch.setattr("stockpilot.demo_persistence.guarded_database", lambda *args: database)
    monkeypatch.setattr("stockpilot.demo_persistence.validate_existing_schema", lambda *args, **kwargs: None)
    client = MagicMock()
    database.client = client
    session = client.start_session.return_value.__enter__.return_value
    session.with_transaction.side_effect = lambda callback, **kwargs: callback(session)

    assert initialize_demo(client, database, dataset) == "inserted"
    session.with_transaction.assert_called_once()
    assert database.create_collection.call_count == 4
    assert sum(database[name].create_index.call_count for name in COLLECTIONS) == 6
    assert {name: len(documents) for name, documents in inserted.items()} == {
        "products": 500, "orders": 2000, "stock_movements": 3000, "demo_manifests": 1,
    }
    for name in COLLECTIONS:
        for call in database[name].insert_many.call_args_list:
            assert len(call.args[0]) <= 500
            assert call.kwargs["session"] is session
        database[name].delete_many.assert_not_called()
        database[name].drop.assert_not_called()
        database[name].replace_one.assert_not_called()


def test_initializer_refuses_a_database_from_a_different_client(dataset):
    client = MagicMock()
    database = database_with({})
    with pytest.raises(ValueError, match="guarded client"):
        initialize_demo(client, database, dataset)
    client.admin.command.assert_not_called()
    database.create_collection.assert_not_called()
