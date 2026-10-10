import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.dto.product_recommendation import ProductRecommendation
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.entities.product import Product
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory


class FakeProductRepository:
    def __init__(self, product: Product | None) -> None:
        self.product = product
        self.find_by_name = AsyncMock(return_value=product)


def run_advice_flow(
    product_name: str | None,
    product: Product | None = None,
) -> tuple[dict, AsyncMock]:
    classify_intent = SimpleNamespace(
        execute=AsyncMock(
            return_value=IntentResult(
                intent=Intent.PRODUCT_ADVICE,
            )
        )
    )
    recommend_product = SimpleNamespace(
        execute=AsyncMock(
            return_value=ProductRecommendation(
                product_name=product_name,
                answer="Voici ma recommandation.",
            )
        )
    )
    product_repository = FakeProductRepository(product)
    check_stock = CheckStockUseCase(product_repository)
    workflow = build_graph(
        classify_intent,
        check_stock,
        recommend_product,
        PrepareOrderUseCase(check_stock),
        SimpleNamespace(execute=AsyncMock()),
        get_order_status=SimpleNamespace(execute=AsyncMock()),
    )
    result = asyncio.run(
        workflow.ainvoke({"message": "Conseilles-moi une perceuse"})
    )
    return result, product_repository.find_by_name


def test_advice_recommendation_includes_stock_quantity() -> None:
    message = (
        "Conseille-moi une perceuse pour béton à moins de 150 € "
        "et vérifie qu’elle est en stock."
    )
    classify_intent = SimpleNamespace(
        execute=AsyncMock(
            return_value=IntentResult(
                intent=Intent.PRODUCT_ADVICE,
                product_name=None,
            )
        )
    )
    recommend_product = SimpleNamespace(
        execute=AsyncMock(
            return_value=ProductRecommendation(
                product_name="Bosch X100",
                answer=(
                    "La Bosch X100 est adaptée au perçage du béton "
                    "et son prix indicatif est de 129,99 €."
                ),
            )
        )
    )
    product_repository = FakeProductRepository(
        Product(
            sku="BOSCH-X100",
            name="Bosch X100",
            category=ProductCategory.DRILL,
            price=129.99,
            stock=12,
        )
    )
    workflow = build_graph(
        classify_intent,
        CheckStockUseCase(product_repository),
        recommend_product,
        PrepareOrderUseCase(CheckStockUseCase(product_repository)),
        SimpleNamespace(execute=AsyncMock()),
        get_order_status=SimpleNamespace(execute=AsyncMock()),
    )
    result = asyncio.run(workflow.ainvoke({"message": message}))

    assert result["response"] == (
        "La Bosch X100 est adaptée au perçage du béton "
        "et son prix indicatif est de 129,99 €.\n\n"
        "Il est actuellement disponible avec 12 unité(s) en stock."
    )
    product_repository.find_by_name.assert_awaited_once_with("Bosch X100")


def test_advice_recommendation_reports_zero_stock() -> None:
    result, check_stock = run_advice_flow(
        "Makita D200",
        Product(
            sku="MAKITA-D200",
            name="Makita D200",
            category=ProductCategory.DRILL,
            price=169.99,
            stock=0,
        ),
    )

    assert result["response"] == (
        "Voici ma recommandation.\n\n"
        "Il est actuellement indisponible."
    )
    check_stock.assert_awaited_once_with("Makita D200")


def test_advice_recommendation_reports_unverified_availability() -> None:
    result, check_stock = run_advice_flow("Bosch X100")

    assert result["response"] == (
        "Voici ma recommandation.\n\n"
        "La disponibilité de ce produit n'a pas pu être vérifiée."
    )
    check_stock.assert_awaited_once_with("Bosch X100")


def test_advice_without_product_name_does_not_check_stock() -> None:
    result, check_stock = run_advice_flow(None)

    assert result["response"] == "Voici ma recommandation."
    check_stock.assert_not_awaited()
