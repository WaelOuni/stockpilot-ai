# Input and model resource limits

This change bounds requests without changing ORDER quantities, prompts, models,
confirmation proofs, Mongo transactions or retry/reconciliation behavior.
It is independent of the unfinished SaaS tenant model. The authentication/config
foundation must keep production SaaS closed until identity, tenant filtering and
distributed usage controls are provisioned and tested. This change alone does
not make the application suitable for public multi-tenant operation.

## Contract and defaults

| Setting | Default | Allowed configuration | Purpose |
|---|---:|---:|---|
| `HTTP_BODY_MAX_BYTES` | 131072 | 128–1048576 | Actual request bytes before dispatch, including requests with missing or false Content-Length |
| `HTTP_BODY_TIMEOUT_SECONDS` | 10 | 0.1–60 | Absolute wall-clock deadline for receiving the bounded body, ending before workflow dispatch |
| `API_MESSAGE_MAX_CHARS` | 8000 | 1–32000 | A conversation instruction, rather than a document upload |
| `API_THREAD_MAX_CHARS` | 128 | 1–256 | Opaque conversation identifier; ample room for UUIDs |
| `API_IDENTIFIER_MAX_CHARS` | 256 | 1–256 | Order/operation path IDs, checked before endpoint use cases; SKU has its separate shared 4096-character ceiling |
| `CONVERSATION_MAX_INFLIGHT` | 4 | 1–32 | Immediate local admission for complete `/assistant`, `/chat`, `/classify` requests |
| `OLLAMA_MAX_PROMPT_BYTES` | 65536 | 1024–1048576 | UTF-8 bytes of complete constructed prompt or embedding input, before opening the HTTP client |
| `OLLAMA_TIMEOUT_SECONDS` | 60 | >0–120 | Each HTTPX connect/read/write/pool inactivity budget |

The 8,000 character default accommodates detailed business instructions while
putting a bound on model input. The 128 KiB body default also accommodates an
8,000 character message encoded with JSON surrogate escapes. The 64 KiB provider
input budget includes the existing classifier template and ordinary messages;
large RAG context may require an explicit increase after capacity review. If
raising message limits, tune the byte budgets for expected JSON/Unicode encoding.
These are conservative local defaults, not measured commercial quotas.

Messages and thread IDs also have schema hard ceilings of 32,000 and 256
characters. Product API metadata names and exact SKUs share the existing CSV
4096-character ceiling through `application/input_limits.py`. The smaller
configurable order/operation identifier bound never applies to SKU paths; a valid
CSV import cannot create a product rejected by a stricter order-ID setting.
Existing bounded search/pagination, stock-adjustment reasons/deltas and CSV
validation remain in place. ORDER quantities continue through the existing
lexical and strict business validation; they are never clamped or rounded here.
Before a future upgrade, inventory unusually long historical SKUs/thread IDs
against the corresponding limits. Existing checkpoints and business identities are not
renamed or migrated by this change; the stable DEMO is not upgraded here.

The existing CSV preview/execute routes retain their separate authorized stream
reader and `PRODUCT_IMPORT_MAX_BYTES` (2 MiB, configurable up to 16 MiB), product
count (maximum 500) and field limits. They are not buffered by the JSON/body
middleware. No new upload or data migration is introduced.

## Rejections and state

The middleware verifies the existing customer JWT before reading any conversation
body, using the same verifier as route dependencies. `/assistant`, `/chat` and
`/classify` therefore require customer authentication at this boundary; the auth
foundation also declares the public API security schemes and keeps SaaS closed.
No new identity store, token mechanism or tenant claim is introduced.

The middleware counts actual streamed bytes before calling the application.
It returns 413 on overflow, including with no Content-Length or a false small
value. Malformed/duplicate size headers return 400. Messages, IDs, invalid payload
types and unexpected fields return 422 before the workflow/model call. Validation
errors omit submitted values, arbitrary field names and validator exception text.
Reception exceeding ten seconds total returns 408 without dispatch; trickling
chunks does not reset the deadline. Ten seconds permits the default 128 KiB body
at roughly 13 KiB/s. Tune the deadline for a controlled local network if required.
An incomplete upload owns no conversation slot, and disconnect/cancellation
before dispatch has no workflow side effect. The existing authorized CSV stream
retains its separate reader; edge upload deadlines remain an operational control.

Caller-supplied history, metadata and tenant identity are unsupported and rejected.
The server persists its own workflow state; there is no client-controlled message
list. A narrow compatibility rule continues to discard the existing inert
`customer_id` body field on `/assistant`. The signed identity remains authoritative;
all other extra fields are forbidden. No checkpoint migration is performed.

Admission happens only after authentication and complete bounded body reception.
Admission has no waiting queue: when the local process already has four complete
conversation requests in progress, an additional one receives 429 with
`Retry-After: 1`. The slot is released in `finally`, including validation failures,
upstream errors and cancellation. This is a process resource bound, not per-user
or per-tenant rate limiting. More workers/pods multiply this bound. Repeated
sequential requests are intentionally not presented as quota enforcement.
When already full, an authenticated request receives 429 before its body is read;
capacity is checked again atomically after reception to cover concurrent uploads.
Anonymous callers receive the normal 401/503 authentication response even when
capacity is full. One authenticated customer can still occupy all local slots;
distributed tenant/customer fairness and quotas remain an explicit SaaS blocker.

Requests rejected before dispatch cannot mutate stock/orders/checkpoints. An
existing prepared proposal remains available for a later valid authenticated
confirmation. Once a valid turn enters the graph, its existing persisted
disarming behavior runs before model calls. Provider timeout/unavailability must
not resurrect that proof after a restart.

## Provider behavior and remaining infrastructure decisions

Ollama transport failures are translated without provider URLs/payloads: timeout
504, unavailability/HTTP failure/invalid JSON 503, prompt byte overflow 413.
Existing deterministic use-case fallbacks remain unchanged. The timeout is an
inactivity limit for each network operation; it is not a total generation or
workflow wall-clock deadline, as defined by the [HTTPX timeout documentation](https://www.python-httpx.org/advanced/timeouts/).
There is no automatic model retry here.

No whole-request timeout cancels an ORDER transaction. A cancelled await or
disconnected caller cannot prove whether a Mongo transaction committed; the
existing operation IDs and reconciliation contract remain essential. Provider
`num_predict`, temperature, prompts and models are unchanged.

Production still needs shared quota/admission accounting keyed by verified
tenant/user, with atomic reservation, expiration/release, configurable budgets
and cross-instance tests. MongoDB/PostgreSQL could support that without a new
Redis service, but a policy and fail-closed distributed backend must be chosen.
Output-token budgets, Ollama capacity/cancellation guarantees, checkpoint retention,
reverse-proxy body/read deadlines and connection limits require operational
validation. No memory counter is advertised as that production guarantee.

The maximum exact Unicode SKU can expand to about 49 KiB when percent-encoded in
a URL. Any proxy serving the full existing product contract needs a request-line
buffer of at least 64 KiB and a compatible header budget. Default Nginx 8 KiB
buffers do not guarantee this. Proxy configuration/real round-trip validation is
tracked separately in the HTTP boundary change; this API change neither truncates
SKUs nor renames existing products. No historical data migration is performed.

## Verification

`test_input_limits.py` checks message/thread/path boundaries, forbidden history,
metadata/tenant spoofing, safe invalid inputs, streamed overflow with absent/false
size headers, pre-body authentication, slow/trickled body deadlines, no slot
ownership by incomplete uploads, immediate concurrent admission, disconnect,
cancellation cleanup and cleanup after provider failures. A workflow lasting
longer than the body deadline still completes normally.
`test_model_resource_limits.py` exercises the actual adapters with an HTTPX mock
transport, including byte limits before client creation, timeouts, unavailability,
malformed provider JSON and preservation of generation payload/configured timeout.

`test_resource_limits_integration.py` uses only the V13.1 guarded fresh Mongo and
PostgreSQL fixtures. It prepares a real persisted proposal, rejects abusive and
over-capacity requests without any mutation, reopens checkpoints, confirms once
and replays idempotently. Separate timeout/unavailability scenarios prove that
disarming persists across restart and that old confirmation cannot reserve stock.
The maximum 4096-character Unicode SKU is imported into fresh MongoDB and read
through an authenticated product route with an eight-character order-ID limit;
its exact name/SKU and zero stock survive retrieval and metadata update.

No live Ollama capacity/failure injection, application database, deployment,
DEMO operation or production quota test is part of these deterministic checks.
