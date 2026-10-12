import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from stockpilot.application.use_cases.resolve_product import (
    ResolveProductUseCase, ResolutionStatus,
)
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.workflows.nodes import create_order_node, create_order_confirmation_node
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.product_category import ProductCategory


def product(sku="DEMO-DRILL-0476", name="Northline Tools 18V Brushless Drill 0476", stock=74):
    return Product(sku, name, ProductCategory.DRILL, 10.0, stock, "507f1f77bcf86cd799439011")


def repository():
    return SimpleNamespace(
        find_resolution_skus=AsyncMock(return_value=[]),
        find_by_sku=AsyncMock(return_value=None),
        find_resolution_candidates=AsyncMock(return_value=[]),
    )


def run(awaitable):
    return asyncio.run(awaitable)


def test_raw_sku_overrides_model_and_missing_model_product():
    repo = repository()
    repo.find_resolution_skus.return_value = [product()]
    for model in (None, "Bosch D461 12V", "Invented product"):
        result = run(ResolveProductUseCase(repo).execute(model, "Je veux commander 1 DEMO-DRILL-0476"))
        assert result.product == product()
        assert result.status == ResolutionStatus.RESOLVED
    repo.find_resolution_candidates.assert_not_awaited()
    repo.find_by_sku.assert_not_awaited()


def test_unknown_sku_never_falls_back_to_another_product():
    repo = repository()
    repo.find_resolution_candidates.return_value = [product()]
    result = run(ResolveProductUseCase(repo).execute("Northline Tools", "Commande DEMO-DRILL-9999"))
    assert result.status == ResolutionStatus.NOT_FOUND
    repo.find_resolution_candidates.assert_not_awaited()


def test_full_user_name_has_priority_over_abbreviated_model_hint():
    repo = repository()
    repo.find_resolution_candidates.return_value = [product()]
    result = run(ResolveProductUseCase(repo).execute("Northline 18V", "Je veux commander 1 " + product().name))
    assert result.product == product()
    repo.find_resolution_candidates.assert_awaited_once_with(product().name, exact=True, limit=2)


@pytest.mark.parametrize("matches", [[], [product(), replace(product(), sku="OTHER")]])
def test_partial_missing_and_ambiguous_are_not_selected(matches):
    repo = repository()
    repo.find_resolution_candidates.side_effect = [[], matches]
    result = run(ResolveProductUseCase(repo).execute("Northline 18V"))
    assert result.product is None
    assert result.status == (ResolutionStatus.AMBIGUOUS if matches else ResolutionStatus.NOT_FOUND)


def test_ungrounded_model_product_cannot_select_catalogue_product():
    repo = repository()
    repo.find_resolution_candidates.return_value = []
    result = run(ResolveProductUseCase(repo).execute("Bosch D461 12V", "Je veux commander une perceuse"))
    assert result.status == ResolutionStatus.NOT_FOUND
    assert all(call.kwargs["exact"] for call in repo.find_resolution_candidates.await_args_list)


@pytest.mark.parametrize("stock,quantity,status", [(0, 1, "OUT_OF_STOCK"), (2, 3, "INSUFFICIENT_STOCK"), (74, 1, "PENDING_CONFIRMATION")])
def test_prepare_has_canonical_identity_without_reservation(stock, quantity, status):
    repo = repository()
    repo.find_resolution_skus.return_value = [product(stock=stock)]
    check = SimpleNamespace(execute=AsyncMock())
    result = run(PrepareOrderUseCase(check, ResolveProductUseCase(repo)).execute(
        None, quantity, message="Je veux commander DEMO-DRILL-0476",
    ))
    assert result.product_sku == "DEMO-DRILL-0476"
    assert result.product_name == product().name
    assert result.status.value == status
    check.execute.assert_not_awaited()


def test_ambiguity_requests_clarification_without_pending_order():
    repo = repository()
    repo.find_resolution_candidates.side_effect = [[], [product(), replace(product(), sku="OTHER")]]
    prepare = PrepareOrderUseCase(SimpleNamespace(), ResolveProductUseCase(repo))
    result = run(create_order_node(prepare)({"product_name": "Northline 18V", "message": "Commander Northline 18V"}))
    assert result["order_status"] == "AMBIGUOUS"
    assert result["pending_order"] is None
    assert "Précisez le SKU" in result["response"]


def test_confirmation_passes_sku_and_enforces_owner_before_finalize():
    finalizer = SimpleNamespace(execute=AsyncMock(return_value=None))
    pending = {"product_document_id": product().document_id, "product_name": product().name, "product_sku": product().sku, "quantity": 1,
               "operation_id": "op", "status": "PENDING_CONFIRMATION", "customer_id": "alice"}
    state = {"pending_order": pending, "order_status": "PENDING_CONFIRMATION", "message": "oui", "customer_id": "bob"}
    with pytest.raises(ValueError, match="autre client"):
        run(create_order_confirmation_node(finalizer)(state))
    finalizer.execute.assert_not_awaited()
    run(create_order_confirmation_node(finalizer)({**state, "customer_id": "alice"}))
    assert finalizer.execute.await_args.kwargs["product_sku"] == product().sku


@pytest.mark.parametrize("message", ["Commande TOOL-UNKNOWN", "Commander SKU: unknown", "Commander sku abc123"])
def test_unknown_explicit_or_labeled_sku_has_no_name_fallback(message):
    repo = repository()
    repo.find_resolution_candidates.return_value = [product()]
    assert run(ResolveProductUseCase(repo).execute("Northline 18V", message)).status == ResolutionStatus.NOT_FOUND
    repo.find_resolution_candidates.assert_not_awaited()


def test_generic_partial_name_is_not_discriminating_even_when_unique():
    repo = repository()
    repo.find_resolution_candidates.side_effect = [[], [product(name="Bosch Jeu de tournevis D461")]]
    assert run(ResolveProductUseCase(repo).execute("jeu de tournevis")).status == ResolutionStatus.NOT_FOUND
