from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from stockpilot.application.dto.intent_result import IntentResult
from stockpilot.application.use_cases.check_stock import StockResult
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.use_cases.generate_response import GenerateResponseUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.domain.enums.intent import Intent
from stockpilot.domain.enums.product_category import ProductCategory
from stockpilot.main import app


def test_chat_uses_generate_use_case(monkeypatch) -> None:
    llm = SimpleNamespace(generate=AsyncMock(return_value="Product advice"))
    use_case = GenerateResponseUseCase(llm)
    monkeypatch.setattr(app.state, "generate_response_use_case", use_case)

    response = TestClient(app).post("/chat", json={"message": "Recommend a drill"})

    assert response.status_code == 200
    assert response.json() == {"response": "Product advice"}
    llm.generate.assert_awaited_once_with("Recommend a drill")


def test_classify_uses_classify_use_case(monkeypatch) -> None:
    result = IntentResult(
        intent=Intent.PRODUCT_ADVICE,
        raw_product="perceuse Bosch X100",
        product_name="Bosch X100",
        budget=125.0,
        usage="home renovation",
    )
    llm = SimpleNamespace(classify_intent=AsyncMock(return_value=result))
    use_case = ClassifyIntentUseCase(llm)
    monkeypatch.setattr(app.state, "classify_intent_use_case", use_case)

    response = TestClient(app).post("/classify", json={"message": "I need a drill"})

    assert response.status_code == 200
    assert response.json() == {
        "intent": "PRODUCT_ADVICE",
        "raw_product": "perceuse Bosch X100",
        "product_name": "Bosch X100",
        "category": "DRILL",
        "budget": 125.0,
        "usage": "home renovation",
        "quantity": None,
    }
    llm.classify_intent.assert_awaited_once_with("I need a drill")


def test_assistant_uses_workflow(monkeypatch) -> None:
    workflow = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "message": "Is it in stock?",
                "intent": "STOCK_CHECK",
                "response": "Stock flow selected for product=None",
            }
        )
    )
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)

    response = TestClient(app).post(
        "/assistant",
        json={"message": "Is it in stock?", "thread_id": "route-thread"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Is it in stock?",
        "intent": "STOCK_CHECK",
        "response": "Stock flow selected for product=None",
    }
    workflow.ainvoke.assert_awaited_once_with(
        {"message": "Is it in stock?"},
        config={"configurable": {"thread_id": "route-thread"}},
    )


def test_assistant_requires_thread_id() -> None:
    response = TestClient(app).post("/assistant", json={"message": "Hello"})

    assert response.status_code == 422


def test_assistant_looks_up_stock_by_canonical_product_name(monkeypatch) -> None:
    message = "Est-ce que la perceuse Bosch X100 est encore disponible ?"
    classification = IntentResult(
        intent=Intent.STOCK_CHECK,
        raw_product="perceuse Bosch X100",
        product_name="Bosch X100",
        category=ProductCategory.DRILL,
    )
    classify_use_case = SimpleNamespace(
        execute=AsyncMock(return_value=classification)
    )
    check_stock_use_case = SimpleNamespace(
        execute=AsyncMock(
            return_value=StockResult(
                sku="BOSCH-X100",
                name="Bosch X100",
                stock=3,
                available=True,
            )
        )
    )
    recommend_product_use_case = SimpleNamespace(execute=AsyncMock())
    workflow = build_graph(
        classify_use_case,
        check_stock_use_case,
        recommend_product_use_case,
        SimpleNamespace(execute=AsyncMock()),
        SimpleNamespace(execute=AsyncMock()),
    )
    monkeypatch.setattr(app.state, "stockpilot_workflow", workflow)

    response = TestClient(app).post(
        "/assistant",
        json={"message": message, "thread_id": "stock-thread"},
    )

    assert response.status_code == 200
    assert response.json()["raw_product"] == "perceuse Bosch X100"
    assert response.json()["product_name"] == "Bosch X100"
    assert response.json()["response"] == (
        "Bosch X100 est disponible avec 3 unité(s) en stock."
    )
    check_stock_use_case.execute.assert_awaited_once_with("Bosch X100")
