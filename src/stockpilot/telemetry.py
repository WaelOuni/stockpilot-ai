from contextlib import contextmanager
from functools import wraps
from threading import Lock
from urllib.parse import urlsplit, urlunsplit
from contextvars import ContextVar
import atexit

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.trace import Status, StatusCode
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.metrics import NoOpMeterProvider
from stockpilot.metrics import initialize_metrics


_provider = None
_meter_provider = None
_lock = Lock()
_errors = ContextVar("tracing_errors", default=None)


class SafeExporter(SpanExporter):
    """Remove exception text and URL credentials/query strings at export."""

    def __init__(self, exporter):
        self.exporter = exporter

    def export(self, spans):
        safe = []
        for span in spans:
            events = tuple(
                Event(event.name, attributes={
                    key: value for key, value in event.attributes.items()
                    if key not in ("exception.message", "exception.stacktrace")
                }, timestamp=event.timestamp)
                for event in span.events
            )
            attributes = dict(span.attributes or {})
            for key in ("url.full", "http.url"):
                if key in attributes:
                    url = urlsplit(attributes[key])
                    host = url.hostname or ""
                    if url.port:
                        host += f":{url.port}"
                    attributes[key] = urlunsplit((url.scheme, host, url.path, "", ""))
            safe.append(ReadableSpan(
                name=span.name, context=span.context, parent=span.parent,
                resource=span.resource, attributes=attributes, events=events,
                links=span.links, kind=span.kind, status=Status(span.status.status_code),
                start_time=span.start_time, end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            ))
        return self.exporter.export(safe)

    def shutdown(self):
        self.exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis)


def initialize_telemetry(settings):
    global _provider, _meter_provider
    if not settings.otel_enabled:
        return None
    with _lock:
        if _provider is None:
            exporter = (
                OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
                )
                if settings.otel_exporter == "otlp"
                else ConsoleSpanExporter()
            )
            _provider = TracerProvider(resource=Resource.create({
                "service.name": settings.otel_service_name,
                "service.version": "0.1.0",
            }))
            _provider.add_span_processor(
                BatchSpanProcessor(SafeExporter(exporter))
            )
            atexit.register(_provider.shutdown)
        if _meter_provider is None:
            metric_exporter = (
                OTLPMetricExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/metrics"
                ) if settings.otel_exporter == "otlp" else ConsoleMetricExporter()
            )
            _meter_provider = MeterProvider(
                resource=_provider.resource,
                metric_readers=[PeriodicExportingMetricReader(
                    metric_exporter,
                    export_interval_millis=settings.otel_metric_export_interval_ms,
                )],
            )
            initialize_metrics(_meter_provider)
            atexit.register(_meter_provider.shutdown)
        return _provider


def instrument_app(app, provider):
    if provider is not None:
        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=provider, exclude_spans=["receive", "send"],
            meter_provider=NoOpMeterProvider(),
        )


def instrument_httpx(client):
    if _provider is not None:
        HTTPXClientInstrumentor.instrument_client(client, tracer_provider=_provider)


def enrich_span(**fields):
    span = trace.get_current_span()
    for key, value in fields.items():
        if value is not None:
            span.set_attribute(key, value)


def trace_fields():
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


@contextmanager
def span(name, **attributes):
    tracer = (_provider or trace.NoOpTracerProvider()).get_tracer("stockpilot")
    token = _errors.set([]) if _errors.get() is None else None
    try:
        with tracer.start_as_current_span(
            name, attributes=attributes, record_exception=False, set_status_on_exception=False
        ) as current:
            try:
                yield current
            except Exception as error:
                recorded = _errors.get()
                if not any(previous is error for previous in recorded):
                    current.record_exception(error, attributes={
                        "exception.message": "[redacted]", "exception.stacktrace": "[redacted]",
                    })
                    recorded.append(error)
                current.set_status(Status(StatusCode.ERROR))
                raise
    finally:
        if token is not None:
            _errors.reset(token)


def traced(name):
    def decorate(function):
        @wraps(function)
        async def call(*args, **kwargs):
            with span(name):
                return await function(*args, **kwargs)
        return call
    return decorate
