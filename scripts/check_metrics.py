"""Export a synthetic low-cardinality metric batch without business services."""

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.telemetry import initialize_telemetry
from stockpilot import telemetry
from stockpilot.metrics import record


def main():
    settings = Settings()
    if not settings.otel_enabled:
        raise RuntimeError("Set OTEL_ENABLED=true to export metrics")
    initialize_telemetry(settings)
    record("http.requests", **{"http.request.method": "GET", "http.route": "/health",
                              "http.response.status_code": 200})
    record("http.request.duration", 0.025, **{
        "http.request.method": "GET", "http.route": "/health", "http.response.status_code": 200,
    })
    record("llm.calls", operation="classify", model="synthetic", outcome="success")
    record("llm.duration", 0.5, operation="classify", model="synthetic", outcome="success")
    record("orders.finalized", outcome="confirmed")
    record("orders.failed", reason="reservation_failed")
    record("mongo.order_transactions", outcome="committed")
    record("mongo.order_transaction.duration", 0.1, outcome="committed")
    record("rag.searches", outcome="empty")
    record("rag.empty_results", outcome="empty")
    telemetry._meter_provider.force_flush()
    print("Synthetic metrics exported; allow Collector batch and Prometheus scrape delay.")


if __name__ == "__main__":
    main()
