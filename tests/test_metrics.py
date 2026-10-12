import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from stockpilot import metrics
from stockpilot.observability import observe_llm
from stockpilot.presentation.api.observability import RequestLoggingMiddleware
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.prepare_order import PrepareOrderUseCase
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.recommend_product import RecommendProductUseCase
from stockpilot.application.order_transaction_errors import OrderTransactionFailedError


@pytest.fixture
def reader(monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(metrics, "_instruments", {})
    metrics.initialize_metrics(provider)
    yield reader
    provider.shutdown()


def points(reader):
    data = reader.get_metrics_data()
    return {metric.name: list(metric.data.data_points)
            for resource in data.resource_metrics
            for scope in resource.scope_metrics for metric in scope.metrics}


def test_http_uses_route_template_and_duration(reader):
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {}

    client = TestClient(app)
    client.get("/items/private-id")
    client.get("/not-a-route-private")
    data = points(reader)
    assert sum(p.value for p in data["stockpilot.http.requests"]) == 2
    assert {p.attributes["http.route"] for p in data["stockpilot.http.requests"]} == {
        "/items/{item_id}", "unmatched",
    }
    assert all(p.sum >= 0 for p in data["stockpilot.http.request.duration"])
    assert "private" not in str(data)


@pytest.mark.parametrize("failure", [False, True])
def test_llm_counts_outcome_and_duration(reader, failure):
    class Llm:
        _settings = SimpleNamespace(ollama_model="qwen")

        @observe_llm("generation")
        async def generate(self, prompt):
            if failure:
                raise RuntimeError("private")
            return "answer"

    if failure:
        with pytest.raises(RuntimeError):
            asyncio.run(Llm().generate("private"))
    else:
        asyncio.run(Llm().generate("private"))
    data = points(reader)
    assert data["stockpilot.llm.calls"][0].value == 1
    assert data["stockpilot.llm.calls"][0].attributes["outcome"] == ("error" if failure else "success")
    assert data["stockpilot.llm.duration"][0].count == 1
    assert ("stockpilot.llm.errors" in data) is failure


@pytest.mark.parametrize("distance,outcome", [(0.2, "results"), (0.8, "empty")])
def test_rag_counts_filtered_empty_results(reader, distance, outcome):
    use_case = RecommendProductUseCase(
        SimpleNamespace(embed=AsyncMock(return_value=[1])),
        SimpleNamespace(search=AsyncMock(return_value=[
            SimpleNamespace(distance=distance, source="source", content="private")
        ])),
        SimpleNamespace(generate=AsyncMock(return_value='{"product_name":null,"answer":"ok"}')),
    )
    asyncio.run(use_case.execute("private"))
    data = points(reader)
    assert data["stockpilot.rag.searches"][0].attributes["outcome"] == outcome
    assert ("stockpilot.rag.empty_results" in data) is (outcome == "empty")


def test_rag_failure_metrics(reader):
    use_case = RecommendProductUseCase(
        SimpleNamespace(embed=AsyncMock(return_value=[1])),
        SimpleNamespace(search=AsyncMock(side_effect=RuntimeError("private-query"))),
        SimpleNamespace(generate=AsyncMock()),
    )
    with pytest.raises(RuntimeError):
        asyncio.run(use_case.execute("private-query"))
    assert points(reader)["stockpilot.rag.searches"][0].attributes == {"outcome": "error"}


def test_stock_unavailable_and_preparation_invalid(reader):
    stock = CheckStockUseCase(SimpleNamespace(find_by_name=AsyncMock(return_value=
        SimpleNamespace(sku="sku", name="private", stock=0, available=False, document_id=None)
    )))
    asyncio.run(stock.execute("private"))
    assert points(reader)["stockpilot.stock.checks"][0].attributes["outcome"] == "unavailable"
    with pytest.raises(ValueError):
        asyncio.run(PrepareOrderUseCase(stock).execute("private", 0))
    assert points(reader)["stockpilot.orders.prepared"][0].attributes["outcome"] == "invalid"


def test_order_confirmed_and_failed(reader):
    finalizer = FinalizeOrderUseCase(SimpleNamespace(execute=AsyncMock(side_effect=lambda order: order)))
    asyncio.run(finalizer.execute("private-product", 2, "private-operation"))
    failure = FinalizeOrderUseCase(SimpleNamespace(execute=AsyncMock(
        side_effect=OrderTransactionFailedError()
    )))
    with pytest.raises(OrderTransactionFailedError):
        asyncio.run(failure.execute("private-product", 2, "private-operation"))
    data = points(reader)
    assert data["stockpilot.orders.finalized"][0].attributes == {"outcome": "confirmed"}
    assert data["stockpilot.orders.failed"][0].attributes == {"reason": "creation_failed"}


def test_transaction_committed_histogram(reader):
    @metrics.observe_transaction
    async def transaction():
        metrics.mark_transaction(started=True)
        metrics.mark_transaction("committed")

    asyncio.run(transaction())
    data = points(reader)
    assert data["stockpilot.mongo.order_transactions"][0].attributes == {"outcome": "committed"}
    assert data["stockpilot.mongo.order_transaction.duration"][0].sum >= 0


@pytest.mark.parametrize("key", [
    "request_id", "thread_id", "trace_id", "span_id", "operation_id", "order_id", "product_name",
])
def test_high_cardinality_attributes_are_rejected(reader, key):
    with pytest.raises(ValueError):
        metrics.record("orders.finalized", **{key: "private"})


def test_disabled_is_transparent(monkeypatch):
    monkeypatch.setattr(metrics, "_instruments", {})
    metrics.record("orders.finalized", outcome="confirmed")


def test_mongo_adapter_committed_and_replay_metrics(reader):
    from test_order_transaction import FakeDatabase, make_order
    from stockpilot.infrastructure.persistence.mongodb.order_transaction import MongoOrderTransaction
    database = FakeDatabase()
    finalizer = FinalizeOrderUseCase(MongoOrderTransaction(database))
    asyncio.run(finalizer.execute("Bosch X100", 3, "operation-123"))
    asyncio.run(finalizer.execute("Bosch X100", 3, "operation-123"))
    data = points(reader)
    assert data["stockpilot.mongo.order_transactions"][0].value == 1
    assert data["stockpilot.mongo.order_transactions"][0].attributes == {"outcome": "committed"}
    assert {p.attributes["outcome"] for p in data["stockpilot.orders.finalized"]} == {
        "confirmed", "replay",
    }
    forbidden = {"request_id", "trace_id", "span_id", "thread_id",
                 "operation_id", "order_id", "product_name"}
    assert all(not (set(point.attributes) & forbidden)
               for series in data.values() for point in series)
