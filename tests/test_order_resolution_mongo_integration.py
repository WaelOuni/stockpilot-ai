"""All writes use the UUID fixture on the explicitly configured test Mongo."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from test_mongo_transaction_integration import database  # noqa: F401
from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.order_idempotency import IdempotencyConflictError
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.resolve_product import ResolveProductUseCase, ResolutionStatus
from stockpilot.application.use_cases.get_order_status import GetOrderStatusUseCase
from stockpilot.application.use_cases.cancel_order import CancelOrderUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.application.workflows.nodes import create_order_confirmation_node
from stockpilot.domain.enums.intent import Intent
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.infrastructure.persistence.mongodb.order_repository import MongoOrderRepository
from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction

pytestmark = pytest.mark.integration


@pytest.fixture
def catalogue(database):
    examples = [
        ("DEMO-DRILL-0476", "Northline Tools 18V Brushless Drill 0476", 74),
        ("SP11-SCR-0081", "Bosch Jeu de tournevis D461 12V", 10),
        ("SHORT-001", "Bosch DEMO06406657", 8),
        ("SPECIAL-001", "Élite Kit [D.4]+ (12V)", 5),
        ("EMPTY-001", "Bosch Empty D000", 0),
    ]
    database.products.insert_many([{"sku": sku, "name": name, "stock": stock,
                                    "category": "DRILL", "price": 10.0} for sku, name, stock in examples])
    return database


def run(awaitable):
    return asyncio.run(awaitable)


def services(db):
    repo = MongoProductRepository(db)
    return (ResolveProductUseCase(repo),
            PrepareOrderUseCase(CheckStockUseCase(repo), ResolveProductUseCase(repo)),
            FinalizeOrderUseCase(MongoOrderTransaction(db)))


@pytest.mark.parametrize("reference,message,sku", [
    (None, "Je veux commander 1 DEMO-DRILL-0476", "DEMO-DRILL-0476"),
    ("Bosch D461 12V", "Commander SP11-SCR-0081", "SP11-SCR-0081"),
    ("demo-drill-0476", None, "DEMO-DRILL-0476"),
    ("Northline Tools 18V Brushless Drill 0476", None, "DEMO-DRILL-0476"),
    ("Bosch D461 12V", "Je veux commander 1 Bosch Jeu de tournevis D461 12V", "SP11-SCR-0081"),
    ("Bosch D461 12V", None, "SP11-SCR-0081"),
    ("Bosch DEMO06406657", None, "SHORT-001"),
    ("  elite   kit [d.4]+ (12v) ", None, "SPECIAL-001"),
])
def test_mongo_literal_resolution_and_preparation_preserve_stock(catalogue, reference, message, sku):
    resolver, prepare, _ = services(catalogue)
    before = list(catalogue.products.find({}))
    resolved = run(resolver.execute(reference, message))
    assert resolved.status == ResolutionStatus.RESOLVED
    assert resolved.product.sku == sku
    prepared = run(prepare.execute(reference, message=message))
    assert prepared.product_sku == sku
    assert prepared.product_name == resolved.product.name
    assert list(catalogue.products.find({})) == before
    assert catalogue.orders.count_documents({}) == 0


def test_similar_products_unknown_sku_and_empty_stock(catalogue):
    resolver, prepare, _ = services(catalogue)
    catalogue.products.insert_one({"sku": "OTHER-0081", "name": "Bosch Jeu de tournevis D461 12V Pro",
                                   "stock": 20, "category": "DRILL", "price": 10})
    assert run(resolver.execute("Bosch D461 12V")).status == ResolutionStatus.AMBIGUOUS
    assert run(prepare.execute("Bosch D461 12V")).status.value == "AMBIGUOUS"
    assert run(resolver.execute("Bosch Jeu de tournevis D461 12V")).product.sku == "SP11-SCR-0081"
    assert run(resolver.execute("Bosch D461 12V", "Commander SP11-SCR-9999")).status == ResolutionStatus.NOT_FOUND
    assert run(prepare.execute("EMPTY-001")).status.value == "OUT_OF_STOCK"
    assert catalogue.orders.count_documents({}) == 0


def test_rename_confirmation_replay_ownership_and_compensation(catalogue):
    _, prepare, finalize = services(catalogue)
    prepared = run(prepare.execute("SP11-SCR-0081", 2))
    catalogue.products.update_one({"sku": prepared.product_sku}, {"$set": {"name": "Renamed"}})
    catalogue.products.insert_one({"sku": "IMPOSTOR-001", "name": prepared.product_name,
                                   "stock": 100, "category": "DRILL", "price": 1})
    def confirm(name=prepared.product_name, owner="alice", sku=prepared.product_sku):
        return run(finalize.execute(name, 2, prepared.operation_id, owner, product_sku=sku, product_document_id=prepared.product_document_id))
    created = confirm()
    assert created.product_sku == prepared.product_sku
    assert catalogue.products.find_one({"sku": prepared.product_sku})["stock"] == 8
    assert catalogue.products.find_one({"sku": "IMPOSTOR-001"})["stock"] == 100
    assert confirm() == created
    assert confirm("Renamed") == created
    with pytest.raises(IdempotencyConflictError):
        confirm(owner="bob")
    with pytest.raises(IdempotencyConflictError):
        confirm(sku="IMPOSTOR-001")
    assert catalogue.orders.count_documents({"operation_id": prepared.operation_id}) == 1
    run(CancelOrderUseCase(MongoOrderRepository(catalogue)).execute(created.order_id))
    assert catalogue.products.find_one({"sku": prepared.product_sku})["stock"] == 10
    assert catalogue.products.find_one({"sku": "IMPOSTOR-001"})["stock"] == 100
    # Cancellation replay must not reserve the product a second time.
    assert confirm().status.value == "CANCELLED"
    assert catalogue.products.find_one({"sku": prepared.product_sku})["stock"] == 10


def test_stock_changed_before_confirmation(catalogue):
    _, prepare, finalize = services(catalogue)
    prepared = run(prepare.execute("DEMO-DRILL-0476", 3))
    catalogue.products.update_one({"sku": prepared.product_sku}, {"$set": {"stock": 2}})
    assert run(finalize.execute(prepared.product_name, 3, prepared.operation_id, "alice",
                                product_sku=prepared.product_sku, product_document_id=prepared.product_document_id)) is None
    assert catalogue.products.find_one({"sku": prepared.product_sku})["stock"] == 2
    assert catalogue.orders.count_documents({}) == 0


def test_old_pending_without_sku_requires_identity_or_recovers_existing(catalogue):
    _, _, finalize = services(catalogue)
    name = "Bosch DEMO06406657"
    pending = {"product_name": name, "quantity": 1, "operation_id": str(uuid4()),
               "status": "PENDING_CONFIRMATION", "customer_id": "alice"}
    state = {"message": "oui", "order_status": "PENDING_CONFIRMATION", "pending_order": pending, "customer_id": "alice"}
    node = create_order_confirmation_node(finalize)
    first = run(node(state))
    assert first["order_status"] == "PRODUCT_IDENTITY_REQUIRED"
    assert catalogue.products.find_one({"sku": "SHORT-001"})["stock"] == 8
    assert catalogue.orders.count_documents({}) == 0
    # Simulate a pre-hotfix operation already committed but not checkpointed.
    existing = run(finalize.execute(name, 1, pending["operation_id"], "alice"))
    catalogue.products.update_one({"sku": "SHORT-001"}, {"$set": {"name": "Renamed"}})
    catalogue.products.insert_one({"sku": "IMPOSTOR-001", "name": name, "stock": 50})
    replay = run(node(state))
    assert replay["created_order_id"] == existing.order_id
    assert replay["order_status"] == "CONFIRMED"
    assert catalogue.products.find_one({"sku": "SHORT-001"})["stock"] == 7
    assert catalogue.products.find_one({"sku": "IMPOSTOR-001"})["stock"] == 50


def test_real_graph_raw_sku_memory_checkpoint_double_confirmation_stock_and_status(catalogue):
    _, prepare, finalize = services(catalogue)
    repo = MongoProductRepository(catalogue)
    classifier = SimpleNamespace(execute=AsyncMock(return_value=IntentResult(
        intent=Intent.ORDER, product_name="Invented", quantity=1,
    )))
    graph = build_graph(classifier, CheckStockUseCase(repo), SimpleNamespace(execute=AsyncMock()),
                        prepare, finalize, MemorySaver(), get_order_status=GetOrderStatusUseCase(MongoOrderRepository(catalogue)))
    config = {"configurable": {"thread_id": str(uuid4())}}
    def invoke(message):
        return run(graph.ainvoke({"message": message, "customer_id": "alice"}, config))
    prepared = invoke("Je veux commander 1 DEMO-DRILL-0476")
    assert prepared["pending_order"]["product_sku"] == "DEMO-DRILL-0476"
    assert catalogue.products.find_one({"sku": "DEMO-DRILL-0476"})["stock"] == 74
    confirmed = invoke("oui")
    assert confirmed["order_status"] == "CONFIRMED"
    classifier.execute.return_value = IntentResult(intent=Intent.UNKNOWN)
    invoke("oui")
    assert catalogue.products.find_one({"sku": "DEMO-DRILL-0476"})["stock"] == 73
    assert catalogue.orders.count_documents({}) == 1
    classifier.execute.return_value = IntentResult(intent=Intent.ORDER_STATUS)
    assert invoke("Statut de ma commande")["queried_order_status"] == "CREATED"
    classifier.execute.return_value = IntentResult(intent=Intent.STOCK_CHECK, product_name="DEMO-DRILL-0476")
    assert "73" in invoke("Stock de DEMO-DRILL-0476")["response"]


def test_model_exact_short_name_cannot_override_full_user_name(catalogue):
    resolver, _, _ = services(catalogue)
    catalogue.products.insert_one({"sku": "SHORTENED-001", "name": "Bosch D461 12V",
                                   "stock": 5, "category": "DRILL", "price": 10})
    # This wording intentionally falls outside the command-prefix parser.
    result = run(resolver.execute("Bosch D461 12V", "Merci de réserver Bosch Jeu de tournevis D461 12V"))
    assert result.product.sku == "SP11-SCR-0081"


def test_duplicate_exact_names_and_skus_refuse_selection(catalogue):
    resolver, _, _ = services(catalogue)
    catalogue.products.insert_one({"sku": "DUPLICATE-001", "name": "Bosch DEMO06406657",
                                   "stock": 5, "category": "DRILL", "price": 10})
    assert run(resolver.execute("Bosch DEMO06406657")).status == ResolutionStatus.AMBIGUOUS
    catalogue.products.insert_one({"sku": "DEMO-DRILL-0476", "name": "Other",
                                   "stock": 5, "category": "DRILL", "price": 10})
    assert run(resolver.execute("DEMO-DRILL-0476")).status == ResolutionStatus.AMBIGUOUS
    with pytest.raises(ValueError, match="Duplicate"):
        run(services(catalogue)[2].execute("Original", 1, str(uuid4()), "alice", product_sku="DEMO-DRILL-0476",
            product_document_id=str(catalogue.products.find_one({"sku": "DEMO-DRILL-0476"})["_id"])))
    assert catalogue.orders.count_documents({}) == 0
    assert catalogue.products.find_one({"name": "Other"})["stock"] == 5


def test_partial_regex_symbols_are_literal_and_queries_bounded(catalogue):
    resolver, _, _ = services(catalogue)
    catalogue.products.insert_one({"sku": "REGEX-002", "name": "Élite Kit [D+4]+ (12V)",
                                   "stock": 5, "category": "DRILL", "price": 10})
    assert run(resolver.execute("Kit [D.4]+")).product.sku == "SPECIAL-001"
    for number in range(25):
        catalogue.products.insert_one({"sku": f"MANY-{number}", "name": f"Similar D200 {number}",
                                       "stock": 1, "category": "DRILL", "price": 10})
    repo = MongoProductRepository(catalogue)
    assert len(run(repo.find_resolution_candidates("Similar D200", exact=False, limit=21))) == 21
    assert run(resolver.execute("Similar D200")).status == ResolutionStatus.AMBIGUOUS


def test_same_sku_concurrent_confirmations_reserve_once(catalogue):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    _, prepare, _ = services(catalogue)
    prepared = run(prepare.execute("DEMO-DRILL-0476", 2))
    barrier = Barrier(4)
    def confirm(_):
        barrier.wait(timeout=10)
        return run(FinalizeOrderUseCase(MongoOrderTransaction(catalogue)).execute(
            prepared.product_name, 2, prepared.operation_id, "alice", product_sku=prepared.product_sku, product_document_id=prepared.product_document_id,
        ))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(confirm, range(4)))
    assert all(result == results[0] for result in results)
    assert catalogue.products.find_one({"sku": prepared.product_sku})["stock"] == 72
    assert catalogue.orders.count_documents({"operation_id": prepared.operation_id}) == 1


def test_quantity_followup_with_real_resolver_keeps_identity(catalogue):
    _, prepare, finalize = services(catalogue)
    repo = MongoProductRepository(catalogue)
    classifier = SimpleNamespace(execute=AsyncMock(side_effect=[
        IntentResult(intent=Intent.ORDER, product_name=None, quantity=1),
        IntentResult(intent=Intent.UNKNOWN),
    ]))
    graph = build_graph(classifier, CheckStockUseCase(repo), SimpleNamespace(execute=AsyncMock()),
                        prepare, finalize, MemorySaver(), get_order_status=GetOrderStatusUseCase(MongoOrderRepository(catalogue)))
    config = {"configurable": {"thread_id": str(uuid4())}}
    first = run(graph.ainvoke({"message": "Commander DEMO-DRILL-0476", "customer_id": "alice"}, config))
    second = run(graph.ainvoke({"message": "J'en veux 2 unités", "customer_id": "alice"}, config))
    assert second["pending_order"]["product_sku"] == first["pending_order"]["product_sku"]
    assert second["pending_order"]["quantity"] == 2


def test_deleted_sku_cannot_be_replaced_by_same_name(catalogue):
    _, prepare, finalize = services(catalogue)
    prepared = run(prepare.execute("SP11-SCR-0081"))
    original = catalogue.products.find_one({"sku": prepared.product_sku})
    catalogue.products.delete_one({"_id": original["_id"]})
    catalogue.products.insert_one({"sku": "REPLACEMENT-001", "name": prepared.product_name, "stock": 10})
    assert run(finalize.execute(prepared.product_name, 1, prepared.operation_id, "alice",
                                product_sku=prepared.product_sku, product_document_id=prepared.product_document_id)) is None
    assert catalogue.products.find_one({"sku": "REPLACEMENT-001"})["stock"] == 10
    assert catalogue.orders.count_documents({}) == 0


def test_distinct_customers_have_distinct_orders(catalogue):
    _, prepare, finalize = services(catalogue)
    orders = []
    for owner in ("alice", "bob"):
        prepared = run(prepare.execute("DEMO-DRILL-0476"))
        orders.append(run(finalize.execute(prepared.product_name, 1, prepared.operation_id, owner,
                                           product_sku=prepared.product_sku, product_document_id=prepared.product_document_id)))
    assert orders[0].operation_id != orders[1].operation_id
    assert orders[0].customer_id == "alice" and orders[1].customer_id == "bob"
    assert catalogue.products.find_one({"sku": "DEMO-DRILL-0476"})["stock"] == 72
