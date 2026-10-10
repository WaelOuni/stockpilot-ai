from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from unittest.mock import patch
import asyncio

import pytest
from pymongo.errors import OperationFailure

from test_mongo_transaction_integration import database, make_order
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
from stockpilot.application.use_cases.manage_products import CreateProductUseCase
from stockpilot.application.use_cases.product_csv_import import (
    PreviewProductCsvImportUseCase, ExecuteProductCsvImportUseCase, CsvImportConflict, CsvImportIndexNotReady,
)
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.product_indexes import prepare_unique_sku_index
from stockpilot.infrastructure.persistence.mongodb.product_import import MongoProductCsvImport, prepare_import_index


pytestmark = pytest.mark.integration
CSV = b"sku,name,category,price\nIMPORT-A,Drill,DRILL,1\nIMPORT-B,Saw,SAW,2\n"


def importer(database, provision=True):
    if provision:
        prepare_unique_sku_index(database.products)
        prepare_import_index(database["product_imports"])
    adapter = MongoProductCsvImport(database)
    preview = PreviewProductCsvImportUseCase(adapter)
    return adapter, preview, ExecuteProductCsvImportUseCase(preview)


def test_real_import_preview_no_writes_atomic_stock_zero_replay_preserves_orders(database):
    adapter, preview, execute = importer(database)
    asyncio.run(MongoOrderTransaction(database).execute(make_order()))
    before_products = list(database.products.find({}))
    before_orders = list(database.orders.find({}))
    approved = preview.execute(CSV, "session")
    assert approved.binding and list(database.products.find({})) == before_products
    operation = str(uuid4())
    created = execute.execute(CSV, "session", operation, approved.binding, True)
    assert created.created == 2 and adapter.result(operation) == created
    assert execute.execute(CSV, "session", operation, approved.binding, True) == created
    assert database.products.count_documents({"sku": {"$in": created.skus}, "stock": 0}) == 2
    assert database["product_imports"].count_documents({}) == 1
    assert list(database.orders.find({})) == before_orders
    for before in before_products:
        assert database.products.find_one({"_id": before["_id"]}) == before
    assert preview.execute(CSV, "session").existing_conflicts == 2


def test_real_commit_ambiguity_reconciles_persisted_receipt_no_reapplication(database):
    adapter, preview, execute = importer(database)
    approved = preview.execute(CSV, "session")
    from pymongo.synchronous.client_session import ClientSession
    original = ClientSession.commit_transaction
    calls = []
    def ambiguous_commit(session):
        original(session)
        calls.append(True)
        raise OperationFailure("simulated lost commit response", details={
            "errorLabels": ["UnknownTransactionCommitResult", "TransientTransactionError"],
        })
    operation = str(uuid4())
    with patch.object(ClientSession, "commit_transaction", ambiguous_commit):
        created = execute.execute(CSV, "session", operation, approved.binding, True)
    assert len(calls) == 1 and adapter.result(operation) == created
    assert database["product_imports"].count_documents({}) == 1
    assert database.products.count_documents({"sku": {"$in": created.skus}}) == 2


def test_real_unknown_uncommitted_stays_unknown_and_leaves_no_partial_products(database):
    _, preview, execute = importer(database)
    approved = preview.execute(CSV, "session")
    from pymongo.synchronous.client_session import ClientSession
    from stockpilot.application.use_cases.product_csv_import import CsvImportUnknown
    with patch.object(ClientSession, "commit_transaction", side_effect=OperationFailure("lost response", details={
        "errorLabels": ["UnknownTransactionCommitResult"],
    })):
        with pytest.raises(CsvImportUnknown):
            execute.execute(CSV, "session", str(uuid4()), approved.binding, True)
    assert database["product_imports"].count_documents({}) == 0
    assert database.products.count_documents({"sku": {"$in": ["IMPORT-A", "IMPORT-B"]}}) == 0


def test_real_missing_receipt_index_fail_closed(database):
    adapter, preview, execute = importer(database, provision=False)
    prepare_unique_sku_index(database.products)
    approved = preview.execute(CSV, "session")
    with pytest.raises(CsvImportIndexNotReady):
        execute.execute(CSV, "session", str(uuid4()), approved.binding, True)
    assert database.products.count_documents({"sku": "IMPORT-A"}) == 0


def test_real_insert_receipt_failure_rolls_back_entire_batch(database):
    adapter, preview, execute = importer(database)
    approved = preview.execute(CSV, "session")
    before = list(database.products.find({}))
    with patch.object(type(adapter.receipts), "insert_one", side_effect=OperationFailure("intentional refusal", 121)):
        with pytest.raises(OperationFailure):
            execute.execute(CSV, "session", str(uuid4()), approved.binding, True)
    assert list(database.products.find({})) == before
    assert database["product_imports"].count_documents({}) == 0


def test_real_concurrent_same_operation_one_receipt(database):
    _, preview, execute = importer(database)
    approved = preview.execute(CSV, "session")
    operation = str(uuid4())
    barrier = Barrier(4)
    def run(_):
        barrier.wait()
        return execute.execute(CSV, "session", operation, approved.binding, True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert all(value == results[0] for value in results)
    assert database.products.count_documents({"sku": {"$in": ["IMPORT-A", "IMPORT-B"]}}) == 2
    assert database["product_imports"].count_documents({}) == 1


def test_real_v41_create_race_no_partial_batch(database):
    adapter, preview, _ = importer(database)
    approved = preview.execute(CSV, "session")
    barrier = Barrier(2)
    def batch():
        barrier.wait()
        try:
            return adapter.execute(str(uuid4()), approved.fingerprint, approved.products)
        except CsvImportConflict:
            return None
    def single():
        barrier.wait()
        try:
            return asyncio.run(CreateProductUseCase(MongoProductRepository(database)).execute(
                "IMPORT-B", "Single saw", ProductCategory.SAW, 3,
            ))
        except Exception as error:
            from stockpilot.application.use_cases.manage_products import DuplicateProductSkuError
            assert isinstance(error, DuplicateProductSkuError)
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        batch_future = pool.submit(batch)
        single_future = pool.submit(single)
        batch_result, single_result = batch_future.result(), single_future.result()
    assert (batch_result is None) != (single_result is None)
    assert database.products.count_documents({"sku": "IMPORT-B"}) == 1
    assert database.products.count_documents({"sku": "IMPORT-A"}) == (1 if batch_result else 0)
    assert database["product_imports"].count_documents({}) == (1 if batch_result else 0)
