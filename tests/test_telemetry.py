import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from stockpilot import telemetry
from stockpilot.observability import event, observe_llm, JsonFormatter
from stockpilot.presentation.api.observability import RequestLoggingMiddleware
from stockpilot.presentation.api.routes.assistant import router
from stockpilot.application.use_cases.finalize_order import FinalizeOrderUseCase
from stockpilot.application.use_cases.recommend_product import RecommendProductUseCase
from stockpilot.infrastructure.config.settings import Settings
from stockpilot import metrics


@pytest.fixture(autouse=True)
def isolate_metric_provider(monkeypatch):
    monkeypatch.setattr(telemetry, "_meter_provider", None)
    monkeypatch.setattr(metrics, "_instruments", {})
    yield
    if telemetry._meter_provider is not None:
        telemetry._meter_provider.shutdown()


@pytest.fixture
def tracing(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(telemetry.SafeExporter(exporter)))
    monkeypatch.setattr(telemetry, "_provider", provider)
    yield provider, exporter
    provider.shutdown()


def test_disabled_does_not_create_provider(monkeypatch):
    monkeypatch.setattr(telemetry, "_provider", None)
    assert telemetry.initialize_telemetry(Settings(otel_enabled=False)) is None
    assert telemetry._provider is None


def test_initializes_one_provider(monkeypatch):
    monkeypatch.setattr(telemetry, "_provider", None)
    first = telemetry.initialize_telemetry(Settings(otel_enabled=True))
    assert telemetry.initialize_telemetry(Settings(otel_enabled=True)) is first
    assert first.resource.attributes["service.name"] == "stockpilot-ai"
    first.shutdown()


@pytest.mark.parametrize("mode", ["console", "otlp"])
def test_exporter_selection_and_single_provider(monkeypatch, mode):
    monkeypatch.setattr(telemetry, "_provider", None)
    console = SimpleNamespace(shutdown=lambda: None)
    otlp = SimpleNamespace(shutdown=lambda: None)
    from unittest.mock import MagicMock
    console_factory = MagicMock(return_value=console)
    otlp_factory = MagicMock(return_value=otlp)
    processor = MagicMock()
    monkeypatch.setattr(telemetry, "ConsoleSpanExporter", console_factory)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", otlp_factory)
    monkeypatch.setattr(telemetry, "BatchSpanProcessor", processor)
    monkeypatch.setattr(telemetry.atexit, "register", MagicMock())
    provider = telemetry.initialize_telemetry(Settings(
        otel_enabled=True, otel_exporter=mode,
        otel_exporter_otlp_endpoint="http://localhost:9999/",
    ))
    assert telemetry.initialize_telemetry(Settings(otel_enabled=True)) is provider
    if mode == "otlp":
        otlp_factory.assert_called_once_with(endpoint="http://localhost:9999/v1/traces")
        console_factory.assert_not_called()
        assert processor.call_args.args[0].exporter is otlp
    else:
        console_factory.assert_called_once_with()
        otlp_factory.assert_not_called()
        assert processor.call_args.args[0].exporter is console
    provider.shutdown()


def test_disabled_never_constructs_exporter(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr(telemetry, "_provider", None)
    factory = MagicMock()
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", factory)
    monkeypatch.setattr(telemetry, "ConsoleSpanExporter", factory)
    assert telemetry.initialize_telemetry(Settings(otel_enabled=False, otel_exporter="otlp")) is None
    factory.assert_not_called()


def test_otlp_endpoint_default_and_environment_override(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert Settings(_env_file=None).otel_exporter_otlp_endpoint == "http://localhost:4318"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:54318")
    monkeypatch.setenv("OTEL_EXPORTER", "otlp")
    settings = Settings(_env_file=None)
    assert settings.otel_exporter_otlp_endpoint == "http://localhost:54318"
    assert settings.otel_exporter == "otlp"


def test_metric_provider_endpoint_interval_and_singleton(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setattr(telemetry, "_provider", None)
    monkeypatch.setattr(telemetry, "_meter_provider", None)
    monkeypatch.setattr(telemetry.atexit, "register", MagicMock())
    monkeypatch.setattr(telemetry, "BatchSpanProcessor", MagicMock())
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", MagicMock())
    exporter = MagicMock()
    factory = MagicMock(return_value=exporter)
    reader = MagicMock()
    meter = MagicMock()
    monkeypatch.setattr(telemetry, "OTLPMetricExporter", factory)
    monkeypatch.setattr(telemetry, "PeriodicExportingMetricReader", reader)
    monkeypatch.setattr(telemetry, "MeterProvider", meter)
    settings = Settings(otel_enabled=True, otel_exporter="otlp",
                        otel_exporter_otlp_endpoint="http://localhost:4318/",
                        otel_metric_export_interval_ms=15000)
    provider = telemetry.initialize_telemetry(settings)
    telemetry.initialize_telemetry(settings)
    factory.assert_called_once_with(endpoint="http://localhost:4318/v1/metrics")
    reader.assert_called_once_with(exporter, export_interval_millis=15000)
    meter.assert_called_once()
    assert meter.call_args.kwargs["resource"] is provider.resource
    provider.shutdown()


def test_server_span_correlates_ids_without_message(tracing):
    provider, exporter = tracing
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(router)
    app.state.stockpilot_workflow = SimpleNamespace(ainvoke=AsyncMock(return_value={
        "intent": "ORDER", "order_status": "CONFIRMED", "created_order_id": "order-a",
        "pending_order": {"operation_id": "op-a"},
    }))
    telemetry.instrument_app(app, provider)
    response = TestClient(app).post(
        "/assistant", json={"message": "private-user-message", "thread_id": "thread-a"}
    )
    server = next(span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER)
    assert server.name == "POST /assistant"
    assert server.attributes["stockpilot.request_id"] == response.headers["X-Request-ID"]
    assert server.attributes["stockpilot.thread_id"] == "thread-a"
    assert server.attributes["stockpilot.operation_id"] == "op-a"
    assert server.attributes["stockpilot.order_id"] == "order-a"
    assert "private-user-message" not in str(server.attributes)


def test_finalize_span_contains_operation_and_order(tracing):
    _, exporter = tracing
    transaction = SimpleNamespace(execute=AsyncMock(side_effect=lambda order: order))
    result = asyncio.run(FinalizeOrderUseCase(transaction).execute("Bosch X100", 2, "op-b"))
    finished = exporter.get_finished_spans()[0]
    assert finished.name == "stockpilot.order.finalize"
    assert finished.attributes["stockpilot.operation_id"] == "op-b"
    assert finished.attributes["stockpilot.order_id"] == result.order_id
    assert finished.attributes["stockpilot.quantity"] == 2
    assert finished.attributes["stockpilot.order_status"] == "CONFIRMED"


def test_rag_span_contains_count_and_distance_without_chunks(tracing):
    _, exporter = tracing
    repository = SimpleNamespace(search=AsyncMock(return_value=[
        SimpleNamespace(distance=0.7, content="private-chunk")
    ]))
    embedding = SimpleNamespace(embed=AsyncMock(return_value=[0.1]))
    use_case = RecommendProductUseCase(embedding, repository, SimpleNamespace(generate=AsyncMock()))
    asyncio.run(use_case.execute("private-question"))
    finished = exporter.get_finished_spans()[0]
    assert finished.name == "stockpilot.rag.search"
    assert finished.attributes["stockpilot.rag.result_count"] == 1
    assert finished.attributes["stockpilot.rag.top_distance"] == 0.7
    assert "private" not in str(finished.attributes)


def test_llm_span_and_log_ids(tracing, caplog):
    _, exporter = tracing

    class FakeLlm:
        _settings = SimpleNamespace(ollama_model="qwen-test")

        @observe_llm("classification")
        async def classify(self, message):
            from stockpilot.domain.enums.intent import Intent
            return SimpleNamespace(intent=Intent.ORDER)

    with caplog.at_level(logging.INFO):
        asyncio.run(FakeLlm().classify("private-prompt"))
    finished = exporter.get_finished_spans()[0]
    assert finished.attributes["gen_ai.request.model"] == "qwen-test"
    assert finished.attributes["stockpilot.intent"] == "ORDER"
    assert "private-prompt" not in str(finished.attributes)
    record = next(record for record in caplog.records if record.event == "llm_classification_completed")
    data = json.loads(JsonFormatter().format(record))
    assert data["trace_id"] == f"{finished.context.trace_id:032x}"
    assert data["span_id"] == f"{finished.context.span_id:016x}"


def test_exception_export_redacts_private_text(tracing):
    _, exporter = tracing
    with pytest.raises(RuntimeError):
        with telemetry.span("outer"):
            with telemetry.span("inner"):
                raise RuntimeError("private-user-message")
    spans = exporter.get_finished_spans()
    assert all(span.status.status_code == StatusCode.ERROR for span in spans)
    assert sum(len(span.events) for span in spans) == 1
    assert "private-user-message" not in str([event.attributes for span in spans for event in span.events])


def test_httpx_client_span_is_child_and_url_query_is_redacted(tracing):
    import httpx

    _, exporter = tracing

    async def run():
        with telemetry.span("stockpilot.llm.generate"):
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"response": "private-answer"})
            )) as client:
                telemetry.instrument_httpx(client)
                await client.post("http://localhost/api/generate?token=private-token",
                                  json={"prompt": "private-prompt"})

    asyncio.run(run())
    spans = exporter.get_finished_spans()
    client = next(span for span in spans if span.kind == SpanKind.CLIENT)
    parent = next(span for span in spans if span.name == "stockpilot.llm.generate")
    assert client.parent.span_id == parent.context.span_id
    assert "private" not in str(client.attributes)


def test_server_error_export_does_not_reveal_exception(tracing):
    provider, exporter = tracing
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    app.include_router(router)
    app.state.stockpilot_workflow = SimpleNamespace(
        ainvoke=AsyncMock(side_effect=RuntimeError("private-exception"))
    )
    telemetry.instrument_app(app, provider)
    response = TestClient(app).post(
        "/assistant", json={"message": "private-input", "thread_id": "thread-error"}
    )
    assert response.status_code == 500
    server = next(span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER)
    assert server.status.status_code == StatusCode.ERROR
    assert "private-exception" not in str([event.attributes for event in server.events])
