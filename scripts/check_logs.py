"""Emit one synthetic correlated log/span through configured local exporters."""

import logging

from stockpilot.infrastructure.config.settings import Settings
from stockpilot.observability import configure_logging, event
from stockpilot.telemetry import initialize_telemetry, span, trace_fields


def main():
    settings = Settings()
    configure_logging(settings.log_level, settings.log_file)
    provider = initialize_telemetry(settings)
    with span("stockpilot.logs.check"):
        event(logging.getLogger("stockpilot.logs.check"), "logs_pipeline_check")
        ids = trace_fields()
    if provider is not None:
        provider.force_flush()
    print(f"Synthetic log emitted. Trace correlation: {ids}")
    print('Explore Loki: {service="stockpilot-ai"} | json | event="logs_pipeline_check"')


if __name__ == "__main__":
    main()
