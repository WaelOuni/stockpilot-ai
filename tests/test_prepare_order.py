import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.check_stock import StockResult
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.domain.enums.order_status import PreparedOrderStatus


def make_check_stock(result):
    return SimpleNamespace(execute=AsyncMock(return_value=result))


def test_available_product_is_prepared_for_confirmation() -> None:
    check_stock = make_check_stock(
        StockResult(
            sku="BOSCH-X100",
            name="Bosch X100",
            stock=12,
            available=True,
        )
    )
    use_case = PrepareOrderUseCase(check_stock)

    result = asyncio.run(use_case.execute("Bosch X100"))

    assert result.product_name == "Bosch X100"
    assert result.quantity == 1
    assert result.available_stock == 12
    assert result.status == PreparedOrderStatus.PENDING_CONFIRMATION
    UUID(result.operation_id)
    second = asyncio.run(use_case.execute("Bosch X100"))
    assert second.operation_id != result.operation_id
    assert check_stock.execute.await_count == 2


def test_zero_stock_product_is_unavailable() -> None:
    check_stock = make_check_stock(
        StockResult(
            sku="MAKITA-D200",
            name="Makita D200",
            stock=0,
            available=False,
        )
    )
    use_case = PrepareOrderUseCase(check_stock)

    result = asyncio.run(use_case.execute("Makita D200"))

    assert result.status == PreparedOrderStatus.OUT_OF_STOCK
    assert result.operation_id is None
    assert result.available_stock == 0


def test_unknown_product_is_reported() -> None:
    check_stock = make_check_stock(None)
    use_case = PrepareOrderUseCase(check_stock)

    result = asyncio.run(use_case.execute("Unknown product"))

    assert result.status == PreparedOrderStatus.PRODUCT_NOT_FOUND
    check_stock.execute.assert_awaited_once_with("Unknown product")


def test_requested_quantity_above_stock_is_rejected() -> None:
    check_stock = make_check_stock(
        StockResult(
            sku="BOSCH-X100",
            name="Bosch X100",
            stock=2,
            available=True,
        )
    )
    use_case = PrepareOrderUseCase(check_stock)

    result = asyncio.run(use_case.execute("Bosch X100", quantity=3))

    assert result.status == PreparedOrderStatus.INSUFFICIENT_STOCK
    assert result.quantity == 3
    assert result.available_stock == 2


def test_zero_and_negative_quantities_are_rejected() -> None:
    check_stock = make_check_stock(
        StockResult(
            sku="BOSCH-X100",
            name="Bosch X100",
            stock=11,
            available=True,
        )
    )
    use_case = PrepareOrderUseCase(check_stock)

    for quantity in (0, -1, 1.5):
        with pytest.raises(ValueError, match="entier strictement positif"):
            asyncio.run(use_case.execute("Bosch X100", quantity=quantity))

    check_stock.execute.assert_not_awaited()


def test_intent_result_accepts_optional_extracted_quantity() -> None:
    result = IntentResult.model_validate_json(
        '{"intent":"ORDER","product_name":"Bosch X100","quantity":3}'
    )

    assert result.quantity == 3


def test_order_workflow_returns_pending_confirmation_without_creating_order() -> None:
    from stockpilot.application.dto.intent_result import IntentResult
    from stockpilot.application.use_cases.check_stock import CheckStockUseCase
    from stockpilot.application.workflows.stockpilot_workflow import build_graph
    from stockpilot.domain.entities.product import Product
    from stockpilot.domain.enums.intent import Intent
    from stockpilot.domain.enums.product_category import ProductCategory

    class FakeProductRepository:
        async def find_by_name(self, name: str) -> Product | None:
            assert name == "Bosch X100"
            return Product(
                sku="BOSCH-X100",
                name="Bosch X100",
                category=ProductCategory.DRILL,
                price=129.99,
                stock=12,
                document_id="fixture-document-BOSCH-X100",
            )

    classify_intent = SimpleNamespace(
        execute=AsyncMock(
            return_value=IntentResult(
                intent=Intent.ORDER,
                product_name="Bosch X100",
            )
        )
    )
    check_stock = CheckStockUseCase(FakeProductRepository())
    recommend_product = SimpleNamespace(execute=AsyncMock())
    prepare_order = PrepareOrderUseCase(check_stock)
    workflow = build_graph(
        classify_intent,
        check_stock,
        recommend_product,
        prepare_order,
        SimpleNamespace(execute=AsyncMock()),
        get_order_status=SimpleNamespace(execute=AsyncMock()),
    )

    result = asyncio.run(
        workflow.ainvoke({"message": "Commande-moi la Bosch X100"})
    )

    assert result["order_status"] == PreparedOrderStatus.PENDING_CONFIRMATION.value
    assert result["pending_order"] == {
        "operation_id": result["pending_order"]["operation_id"],
        "product_name": "Bosch X100",
        "product_sku": "BOSCH-X100",
        "product_document_id": "fixture-document-BOSCH-X100",
        "quantity": 1,
        "status": "PENDING_CONFIRMATION",
    }
    UUID(result["pending_order"]["operation_id"])
    assert result["response"] == (
        "La Bosch X100 est disponible avec 12 unités en stock. "
        "Confirmez-vous la commande d’une Bosch X100 ?"
    )
