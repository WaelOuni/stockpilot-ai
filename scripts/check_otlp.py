"""Send one synthetic span; verify the Collector acknowledges OTLP ingestion."""

import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExportResult

from stockpilot.telemetry import SafeExporter


def main():
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    provider = TracerProvider(resource=Resource.create({"service.name": "stockpilot-ai"}))
    exporter = SafeExporter(OTLPSpanExporter(
        endpoint=endpoint.rstrip("/") + "/v1/traces", timeout=10,
    ))
    try:
        with provider.get_tracer("stockpilot.otlp.check").start_as_current_span(
            "stockpilot.otlp.check", attributes={"stockpilot.synthetic": True}
        ) as span:
            trace_id = f"{span.get_span_context().trace_id:032x}"
        result = exporter.export([span])
        if result != SpanExportResult.SUCCESS:
            raise RuntimeError("Collector did not acknowledge the OTLP span")
        print(f"Collector accepted span; trace_id={trace_id}")
        print("Acceptance is not proof of Tempo storage; find this trace in Grafana.")
    finally:
        exporter.shutdown()
        provider.shutdown()


if __name__ == "__main__":
    main()
