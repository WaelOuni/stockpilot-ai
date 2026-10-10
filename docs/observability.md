# Observability

[Back to README](../README.md) · [Getting started](getting-started.md) · [Order flow](order-flow.md)

All commands run from the project root in WSL/Linux. This is a local/demo stack,
not a production monitoring deployment. Logs, traces and metrics are best-effort
diagnostics, not business accounting or exactly-once durable delivery.

## Pipelines and services

```text
API -> stdout JSON + logs/stockpilot.jsonl -> Alloy -> Loki -> Grafana
API -> OTLP HTTP /v1/traces -> Collector contrib -> Tempo -> Grafana
API -> OTLP HTTP /v1/metrics -> Collector :8889 <- Prometheus <- Grafana
Blackbox -> GET /health (Docker-to-host) -> Prometheus rules -> Grafana
                                   Loki <-> Tempo via trace_id
```

Logs reach Loki through Alloy's read-only file mount, **not** the Collector.
The current [Collector config](../docker/otel-collector-config.yaml) has traces
and metrics pipelines only. Collector contrib 0.123.0 includes the Prometheus
exporter (the core image does not). Other pinned images: Tempo 2.7.2,
Grafana 11.6.0, Alloy v1.8.3, Loki 3.4.2, Prometheus v3.2.1,
Blackbox Exporter v0.26.0; see [Compose](../docker-compose.yml).

Only Collector HTTP `127.0.0.1:4318` and Grafana `127.0.0.1:3000` are published
for telemetry. Collector gRPC/health 13133, Tempo HTTP 3200/OTLP, Loki, Alloy,
Prometheus, metrics 8889 and Blackbox stay inside Docker.
Grafana starts with **admin / admin** and prompts for a password change;
the changed password persists in its volume. Do not expose this stack publicly.

## JSON logs and privacy

Stdlib application logging emits JSON lines with UTC timestamp, level, logger,
event and contextual fields. No telemetry service is required.
`LOG_LEVEL=DEBUG` changes default INFO verbosity; configuration applies to
`stockpilot`, separately from Uvicorn/third-party loggers.

| Identifier | Meaning |
|---|---|
| `request_id` | UUID per HTTP request, returned as `X-Request-ID` |
| `thread_id` | Caller-supplied assistant conversation ID |
| `operation_id` | Stable logical order operation, including after restart |
| `order_id` | Persisted Mongo order ID |
| `trace_id` / `span_id` | OTel trace/span IDs when an active span exists |

Async correlation uses ContextVar, reset at the HTTP boundary. Events include
HTTP/assistant start, completion/failure; intent; LLM classification/generation;
RAG result count/distance; stock; confirmation/replay/transaction/finalization;
conflicts and unknown outcomes. Checkpoint events cover initialization, startup
failure and closure only. Timings use `perf_counter()` in milliseconds.

Illustrative log:

```json
{"timestamp":"2026-10-09T21:40:00+00:00","level":"INFO","logger":"stockpilot.application.workflows.nodes","event":"order_finalized","request_id":"request-uuid","thread_id":"thread-1","operation_id":"operation-uuid","order_id":"order-uuid","product_name":"Bosch X100","quantity":2,"order_status":"CONFIRMED","duration_ms":143.2}
```

Bodies, user messages, prompts, model responses and RAG chunks are not logged.
Application JSON stacktraces contain exception type and frame location/function,
not exception text, source lines or local values. Unexpected HTTP failures
return a generic 500 body and request ID. Product names/business IDs remain
private application data. Third-party logs need a separate privacy review.
Local file rotation and Loki retention/forwarding **are configured below**;
there is no configured Datadog/ELK forwarding.

The default file is `logs/stockpilot.jsonl`, relative to the working directory.
Start Uvicorn from the project root to match the Compose mount. `LOG_FILE`
overrides the path; `LOG_FILE=''` disables file logs. Adjust the bind mount when
changing paths. RotatingFileHandler keeps a 10 MiB active file and three backups
(about 40 MiB). Records are original JSON, one per line. Git ignores real logs/
backups; only `logs/.gitkeep` is tracked. Configuration is idempotent and
file-open errors fail startup.

## OpenTelemetry tracing

Tracing is disabled by default, including standard tests. `OTEL_SERVICE_NAME`
defaults to `stockpilot-ai`. `OTEL_EXPORTER=console` prints locally (Settings
default); `otlp` sends HTTP/protobuf to the local stack. `.env.example` selects
OTLP but leaves telemetry disabled. No external service is required for disabled
or console mode.

```sh
export OTEL_ENABLED=true
export OTEL_SERVICE_NAME=stockpilot-ai
export OTEL_EXPORTER=console
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

A single process-local provider initializes before official FastAPI ASGI
instrumentation, uses a BatchSpanProcessor, flushes at lifespan shutdown and
shuts down at process exit. Restart to change configuration. Resources include
service name/version. Ollama httpx clients are explicitly instrumented without
globally instrumenting unrelated clients. FastAPI creates the server span;
manual spans cover `stockpilot.llm.classify`, `stockpilot.llm.generate`,
`stockpilot.rag.search`, `stockpilot.stock.check`, `stockpilot.order.prepare`,
`stockpilot.order.finalize`, `stockpilot.mongo.order_transaction`.
There is no Mongo/Psycopg auto-instrumentation or internal-checkpoint tracing.

```text
POST /assistant                         trace_id=<32 hex characters>
  stockpilot.order.finalize             operation_id=..., quantity=2
    stockpilot.mongo.order_transaction
```

Server attributes include `stockpilot.request_id`, `stockpilot.thread_id` and
known operation/order IDs. Finalization adds product/quantity/status; LLM spans
add model/intent; RAG adds count/top distance. `trace_id` is OTel-generated or
caller-propagated, not replaced by thread/operation IDs. Active-span logs include
32-character hex trace ID and 16-character span ID.

No bodies/prompts/responses/chunks are attached. Application exception events
are redacted. The export-boundary privacy filter removes exception text/
stacktraces, status descriptions and URL credentials/query parameters, including
automatic instrumentation. Console and OTLP use the same filter. Product names/
business IDs need retention/access policies. Do not enable sensitive HTTP header
capture or put credentials in URL paths. Sampling/production policy remains work.
Tracing measures synchronous PyMongo latency but does not unblock the event loop.

Tests use an official in-memory exporter/provider, without a Collector:

```sh
.venv/bin/python -m pytest -q tests/test_telemetry.py
```

## Local tracing stack and diagnostics

```text
API (WSL) -> localhost:4318/v1/traces -> Collector
  -> OTLP gRPC tempo:4317 -> Tempo local storage <- Grafana
```

Python uses `opentelemetry-exporter-otlp-proto-http==1.45.1`, matching API/SDK,
not the Python gRPC exporter.

```sh
docker compose up -d otel-collector tempo grafana
docker compose ps otel-collector tempo grafana
docker compose logs --tail=30 otel-collector tempo grafana
docker compose exec -T grafana wget -qO- http://tempo:3200/ready
docker compose exec -T grafana wget -qO- http://otel-collector:13133/
```

Grafana has a wget healthcheck. Collector/Tempo images lack reliable in-image
curl/wget probes; no invented shell healthcheck is used. `depends_on` orders
startup, not readiness; allow startup time and inspect endpoints/logs.

Start the API in WSL with its normal database/Ollama environment:

```sh
export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_SERVICE_NAME=stockpilot-ai
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

The endpoint is a **base URL**: the app appends `/v1/traces` or `/v1/metrics`.
From WSL use localhost, not Docker's `otel-collector` hostname. Different Docker
forwarding requires an address reachable from WSL; retain loopback binding
unless intentionally changing the security boundary. `OTEL_ENABLED=false`
overrides exporter selection; exporter/endpoint changes require API restart.

```sh
curl -X POST http://localhost:8000/assistant \
  -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Est-ce que Bosch X100 est disponible ?","thread_id":"tracing-demo-1"}'
.venv/bin/python scripts/check_otlp.py
```

The synthetic script sends one span, fails if Collector does not acknowledge,
and prints its trace ID. Acceptance is not downstream storage proof; batching/
search visibility takes seconds. Check Collector logs on failure.

At http://localhost:3000, **Explore -> Tempo**, use TraceQL:

```traceql
{ resource.service.name = "stockpilot-ai" }
{ span.stockpilot.thread_id = "tracing-demo-1" }
{ span.stockpilot.operation_id = "your-operation-id" }
```

Choose a recent range or paste a trace ID into trace-ID lookup. Inspect server
and LLM/RAG/ORDER children. Tempo UID `stockpilot-tempo` targets
`http://tempo:3200`; no metrics generator is configured.

Tempo's `tempo_data` stores WAL/blocks with 24-hour retention; `grafana_data`
stores Grafana settings. Collector queues are memory-backed: outages/restarts
can lose unexported traces. Export is not durable and must not gate business
operations. OTLP is local, unauthenticated, without TLS/HA. Review authentication,
TLS, images, backup, retention and sampling before production.

```sh
docker compose stop otel-collector tempo grafana
```

Stopping/recreating preserves volumes; do not use `down -v`.

## Alloy/Loki logs and trace correlation

Alloy v1.8.3 replaces Promtail. Loki 3.4.2 uses local TSDB/filesystem storage in
`loki_data` with seven-day retention. Alloy positions survive recreation in
`alloy_data`. Neither publishes a host port.

```sh
docker compose up -d loki alloy tempo otel-collector grafana
export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

Generate a fresh-thread request or a synthetic log/span:

```sh
.venv/bin/python scripts/check_logs.py
docker compose logs --tail=30 alloy loki
docker compose exec -T grafana wget -qO- http://loki:3100/ready
```

Allow startup/ingestion time. Grafana provisioning applies at startup; after
changing provisioning files use `docker compose up -d --force-recreate grafana`.
Loki/Alloy have no invented curl healthchecks; inspect logs and Loki readiness.

In Grafana **Explore -> Loki**, indexed labels are only
`service="stockpilot-ai"`, `environment="local"`, `level`; filename is dropped.
Loki derives low-cardinality `service_name` from `service`. Logger/event and all
high-cardinality IDs remain in the original JSON, not indexed stream labels.

```logql
{service="stockpilot-ai"}
{service="stockpilot-ai", level="ERROR"}
{service="stockpilot-ai", level=~"ERROR|CRITICAL"}
{service="stockpilot-ai"} | json | operation_id="your-operation-id"
{service="stockpilot-ai"} | json | thread_id="your-thread-id"
{service="stockpilot-ai"} | json | trace_id="your-32-character-trace-id"
{service="stockpilot-ai"} | json | event="logs_pipeline_check"
```

CRITICAL is distinct from ERROR. Expand a log with a lowercase hex `trace_id`
and choose **View Trace** to follow the derived field to Tempo.
From a Tempo span use its logs link: `tracesToLogsV2` maps resource `service.name`
to Loki `service` and filters parsed JSON by trace ID in a +/- one-minute window.
See [Loki datasource](../docker/grafana/provisioning/datasources/loki.yaml)
and [Tempo datasource](../docker/grafana/provisioning/datasources/tempo.yaml).
Without tracing there is no trace link; retention/sampling/export failures can
leave a log without a stored trace. IDs must not become Loki labels.

**Rotation/delivery caveats:** Alloy reads active `*.jsonl`, not `.jsonl.1`
backups, avoiding rediscovery/duplicates. Its tailer follows normal rename/create
rotation, but long outages plus multiple rotations can lose unread logs, and
position loss can replay duplicates. RotatingFileHandler is not multi-process
safe: use one API worker, separate files or a suitable centralized strategy.
Do not simultaneously write/rotate the same file from multiple processes.
The service label is fixed; update Alloy and trace service.name together when
renaming. Application JSON excludes Uvicorn/third-party logs; product names/IDs
are still private. This is dev-only Loki without TLS/auth.

```sh
docker compose stop alloy loki otel-collector tempo grafana
```

## Metrics

Logs describe events, traces request paths, metrics aggregated trends.
StockPilot uses only OpenTelemetry metrics, not a second Python Prometheus
instrumentation stack. The installed HTTP/protobuf exporter supplies
OTLPMetricExporter. One MeterProvider shares the tracing resource and exports
every 10 seconds (`OTEL_METRIC_EXPORT_INTERVAL_MS`, positive integer).
Console mode exports locally; disabled mode records nothing.
Collector's metrics pipeline is OTLP -> batch -> Prometheus exporter `:8889`.
Traces remain OTLP -> batch -> Tempo; there is no Collector logs pipeline.
Prometheus stores seven days in `prometheus_data`.

Durations are **seconds**, unlike log `duration_ms`. Exported counters end in
`_total`; histograms use `_seconds_bucket`, `_seconds_sum`, `_seconds_count`.

| OTel instrument | Attributes |
|---|---|
| stockpilot.http.requests / http.request.duration | http.request.method, http.route, http.response.status_code |
| stockpilot.llm.calls / llm.duration / llm.errors | model, operation=classify/generate, outcome=success/error |
| stockpilot.rag.searches / rag.duration | outcome=results/empty/error |
| stockpilot.rag.empty_results | outcome=empty |
| stockpilot.stock.checks | outcome=available/unavailable/not_found |
| stockpilot.orders.prepared | outcome=pending_confirmation/insufficient_stock/invalid |
| stockpilot.orders.finalized | outcome=confirmed/replay |
| stockpilot.orders.failed | reason=reservation_failed/creation_failed/outcome_unknown/idempotency_conflict |
| stockpilot.mongo.order_transactions / mongo.order_transaction.duration | outcome=committed/aborted/error |

RAG empty means no document passed the relevance filter, not no raw DB results.
Transaction metrics aggregate driver retries into one adapter call and exclude
precheck replays. Finalizations count successful attempts including recovered
replays, not unique global orders. Prepared invalid includes missing/unknown
products or invalid quantity. A shared whitelist forbids business/trace IDs,
product names, prompts and queries in metric attributes. HTTP routes are
templates or `unmatched`, never raw paths. Official FastAPI/httpx metrics are
disabled to avoid duplication/uncontrolled URLs; their traces remain active.

```sh
docker compose up -d loki alloy tempo otel-collector prometheus grafana
export OTEL_ENABLED=true
export OTEL_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
.venv/bin/python -m uvicorn stockpilot.main:app --reload
OTEL_ENABLED=true OTEL_EXPORTER=otlp .venv/bin/python scripts/check_metrics.py
```

The last command produces **synthetic dev-only** series, including model
`synthetic`, without Mongo/Ollama. Allow export (10s), batch and scrape (10s)
delay. Rate/p95 panels need at least two scrapes and ongoing traffic.
Empty panels on an idle system do not imply failure.

Grafana **Dashboards -> StockPilot -> StockPilot Overview** has nine panels:
HTTP rate/p95/5xx ratio, LLM rate/p95, ORDER finalizations/failures, Mongo p95,
RAG empty ratio. Dashboard/datasource provisioning applies on creation/restart.

Verified exported PromQL:

```promql
sum(rate(stockpilot_http_requests_total[5m]))
histogram_quantile(0.95, sum by (le) (rate(stockpilot_http_request_duration_seconds_bucket[5m])))
sum(rate(stockpilot_llm_errors_total[5m]))
sum by (outcome) (rate(stockpilot_orders_finalized_total[5m]))
sum by (reason) (rate(stockpilot_orders_failed_total[5m]))
```

Attribute dots normalize to underscores, e.g. `http_response_status_code`.
Exporter/scrape job and instance labels identify service/process, not requests.
Counters reset on restart. HTTP duration ends at response headers, not completion
of streaming bodies. SDK histogram boundaries are defaults; tune after workload
observation. Local SLO/Prometheus alert rules **are configured below**;
Alertmanager and notifications are not.

## SLI/SLO and local alerts

SLI is a measurement, SLO its target, alert a sustained-deviation signal.
These provisional dev/demo rolling five-minute objectives are not contractual
monthly SLOs. Rules aggregate API instances with `exported_job="stockpilot-ai"`.

| Recording rule | Definition | Target |
|---|---|---|
| stockpilot:sli:assistant_availability:5m | non-5xx / all `/assistant` requests | >=99% |
| stockpilot:sli:assistant_p95_latency:5m | p95 `/assistant` HTTP histogram | <2 seconds |
| stockpilot:sli:order_success_rate:5m | finalized / (finalized + failed) | >=95% |
| stockpilot:sli:llm_success_rate:5m | 1 - errors / calls | >=95% |
| stockpilot:sli:mongo_transaction_success_rate:5m | committed / all outcomes | >=99% |

Exact executable PromQL is in [prometheus-rules.yml](../docker/prometheus-rules.yml),
using `http_route`, `http_response_status_code`, `outcome` and verified exported
counter/histogram names. Missing error series are zero; missing success series
are zero when failures exist. Positive-denominator gates make no traffic
**no series**, not NaN/Inf or invented 100%. Latency requires observations.

ORDER includes replay successes/expected reservation refusals. Mongo measures
adapter calls, not retry attempts; aborted stock refusals lower its success rate.
Availability counts 4xx and HTTP 200 business refusals as available.
Recalibrate/split these distinct concepts after realistic workload observation.

**StockPilot SLO** shows five target-labelled stats (green OK, orange breach,
absent `No traffic`) and availability error rate against a 1% allowance,
plus Blackbox reachability/duration panels. No monthly budget or multi-window
burn rate is claimed. Exactly two seconds breaches the `<2` latency target.

Prometheus evaluates every 10 seconds. These warning alerts hold for five
continuous minutes: `AssistantHighErrorRate`, `AssistantHighLatency`,
`OrderFailureRateHigh`, `LLMErrorRateHigh`, `MongoTransactionFailure`.
No traffic, missing metrics or stopped API does not fire **ratio** alerts;
the current independent [Blackbox probe](#external-availability-blackbox)
handles liveness and missing-probe alerts. No Alertmanager/notifications exist.

```sh
docker compose up -d --force-recreate prometheus grafana
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/assistant \
    -H "Authorization: Bearer $CUSTOMER_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"message":"Est-ce que Bosch X100 est disponible ?","thread_id":"slo-demo"}'
done
docker compose exec -T grafana wget -qO- http://prometheus:9090/api/v1/rules
docker compose exec -T grafana wget -qO- http://prometheus:9090/api/v1/alerts
```

Allow export/scrape/evaluation. LLM requests naturally may exceed two seconds;
the target is provisional. Grafana Explore/Prometheus can query
`ALERTS{alertstate="firing"}`. Rules are Prometheus-managed, not duplicate Grafana
alerts. Synthetic rule fixtures avoid disrupting dependencies/business data:

```sh
docker compose run --rm --no-deps --entrypoint promtool prometheus \
  check config /etc/prometheus/prometheus.yml
docker run --rm --entrypoint promtool \
  -v "$PWD/docker:/rules:ro" -w /rules prom/prometheus:v3.2.1 \
  test rules prometheus-rules-tests.yml
```

Fixtures cover no traffic, healthy traffic without error series and sustained
five-alert breaches/hold periods. A live latency demonstration needs ongoing
requests for at least five minutes with p95 >2s; do not stop dependencies or
inject false live metrics merely to demonstrate alerts. Best-effort exports,
scrapes, counter resets, histogram approximation and low-volume noise limit
SLOs; no minimum sample-volume policy exists.

## External availability: Blackbox

Traffic availability cannot detect process downtime when no requests/metrics
arrive. Blackbox independently probes GET `/health` every 10 seconds without
Ollama or user traffic. Target:
**`http://host.docker.internal:8000/health`**.

This Docker-to-host path reached loopback-bound WSL Uvicorn in the previously
verified Docker Desktop environment without dynamic WSL IP/`extra_hosts`.
Verify it on your installation:

```sh
docker compose exec -T prometheus wget -S -O - -T 5 \
  http://host.docker.internal:8000/health
```

Native Docker Engine may need `host.docker.internal:host-gateway` in an
`extra_hosts` entry on `blackbox-exporter`. That fixes DNS, not forwarding/
firewalls. Without Desktop loopback forwarding, bind Uvicorn to a reachable
interface (e.g. `--host 0.0.0.0`) only after considering exposure/firewall.
Do not hardcode an ephemeral WSL address.

```sh
docker compose up -d blackbox-exporter prometheus grafana
# Compose may leave an already-running Prometheus unchanged:
docker compose restart prometheus
# API terminal, preserving its usual environment:
.venv/bin/python -m uvicorn stockpilot.main:app --reload
```

[blackbox.yml](../docker/blackbox.yml) specifies IPv4 GET, five-second timeout,
no redirects, only direct 2xx success. Connection/timeout/non-2xx (including
5xx) gives `probe_success=0`. Prometheus scrapes `/probe` at
`blackbox-exporter:9115` with an eight-second scrape timeout. No new host ports.

```sh
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/query?query=probe_success%7Bjob%3D%22stockpilot-blackbox%22%7D'
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/rules?type=alert'
docker compose exec -T prometheus wget -qO- \
  'http://localhost:9090/api/v1/alerts'
```

Grafana Explore/Prometheus: `probe_success{job="stockpilot-blackbox"}` or
`ALERTS{alertname=~"StockPilotUnavailable|BlackboxProbeMissing"}`.
**StockPilot SLO** shows Reachability UP/DOWN (absent UNKNOWN) and duration.

| Alert | Expression | Hold | Severity |
|---|---|---|---|
| StockPilotUnavailable | `probe_success{job="stockpilot-blackbox"} == 0` | 1m | critical |
| BlackboxProbeMissing | `absent(probe_success{job="stockpilot-blackbox"})` | 2m | warning |

Manual UP/DOWN/recovery:

1. Start API; verify probe=1, alerts inactive.
2. Ctrl+C Uvicorn, including reload supervisor.
3. Verify probe=0; after one continuous minute plus 10s scrape/evaluation delays,
   StockPilotUnavailable fires.
4. Restart the same API/environment; probe=1 resolves on next evaluation.

To test monitoring absence independently, `docker compose stop blackbox-exporter`;
failed scrape removes probe series and the missing-probe alert fires after two
minutes. Restart with `docker compose up -d blackbox-exporter`.
This is not proof of API downtime; it may briefly be pending during startup.

Validate without stopping API or launching services in pytest:

```sh
.venv/bin/python -m pytest -q tests/test_slo_configuration.py tests/test_health.py
docker compose config --quiet
docker compose run --rm --no-deps --entrypoint promtool prometheus \
  check config /etc/prometheus/prometheus.yml
docker compose run --rm --no-deps -v "$PWD/docker:/rules:ro" \
  --entrypoint promtool prometheus test rules /rules/prometheus-rules-tests.yml
```

Fixtures cover UP, sustained DOWN, resolution and absent series/hold periods.
`/health` returns `{"status":"UP"}` for process liveness, **not**
Mongo/PostgreSQL/Ollama readiness; Blackbox does not validate the body.
It observes the Docker-to-host path, not public Internet availability.
If Prometheus is down, no rule evaluates; monitoring the monitor needs a separate
system. No Alertmanager or notifications are configured.
