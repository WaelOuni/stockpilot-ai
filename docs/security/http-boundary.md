# HTTP security boundary

V13.2 mini-RFC, baseline `6f094967`. Scope: HTTP configuration and browser
headers only. Authentication, tenant schemas, ORDER and deployment remain separate.

The baseline API accepts arbitrary Host values and does not enforce HTTPS for
customer routes. Its tracked web proxy is an HTTP catch-all. Only backoffice
responses currently prohibit caching. The built Vue app has no external runtime
resources or inline style attributes, so a same-origin CSP fits the current app.
These observations concern repository artifacts; no running DEMO was modified or
probed to establish them.

## Decision

Add a small ASGI boundary installed by the composition root. In production,
operators must configure exact allowed hosts, with no wildcard. Until configured,
business routes return 503; a minimal loopback-host `/health` remains available.
Configured production requests require ASGI HTTPS except `/health` for internal
probes. Raw `Forwarded` or `X-Forwarded-*` headers never satisfy this requirement.
Trusted ASGI-server proxy configuration is an operational prerequisite.

The frontend and API use the same public origin by default. No CORS allowlist is
needed. Cross-origin browser requests in production are refused unless their
exact HTTPS origin is explicitly configured. An explicit CORS list supports a
future split frontend; it does not grant authentication or tenant permissions.
Admin cookies still require their existing exact Origin and session CSRF checks.

The API owns `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and
`Referrer-Policy: no-referrer`. The web server owns the SPA CSP and anti-framing
headers. This avoids attaching an irrelevant CSP to JSON or duplicating browser
document policies on the backend. Build output must contain only local scripts
and styles compatible with `script-src 'self'; style-src 'self'`.

TLS termination and optional HSTS belong to the actual public edge. HSTS remains
unset here: no public domain, TLS chain or subdomain policy has been selected.
Do not enable preload/includeSubDomains on localhost or without an operational
decision. The tracked development Compose is not a SaaS deployment template.

## Proxy contract

Only the TLS edge may reach a production backend. Restrict Uvicorn's trusted proxy
addresses to those peers; do not trust arbitrary forwarded headers or expose the
backend publicly. Each proxy hop must sanitize its incoming forwarding metadata.
The included web server sends its own HTTP scheme, so an external TLS edge must
route `/api` directly to the backend or use a separately reviewed private proxy
chain that preserves the original HTTPS scheme. Changing a raw client header is
never an alternative to configuring that chain.

Health remains deliberately minimal and unauthenticated. It is not a readiness
or infrastructure diagnostic and reveals no database, principal or tenant state.
Host validation still applies when a production allowlist is configured; include
the actual probe host or use its configured Host header.

Configuration values are environment JSON arrays, for example
`HTTP_TRUSTED_HOSTS='["app.example.test","127.0.0.1"]'`. Hosts contain no scheme
or port. Leave `HTTP_ALLOWED_ORIGINS` unset for a same-origin app. If a separate
frontend is required, use an exact serialized browser origin such as
`HTTP_ALLOWED_ORIGINS='["https://frontend.example.test"]'`; no wildcard, trailing
slash, credentials, path or query is permitted. All allowed origins must use
HTTPS in production. These values do not enable SaaS authentication or tenancy.

## Validation and compatibility

Tests must cover unexpected hosts, missing configuration, plain HTTP, forged
forwarding headers, forbidden origins, explicit CORS preflight, credentialed
origins and security headers on errors. Existing local development behavior stays
compatible. Production smoke tests must prove business APIs are closed until the
security prerequisites exist, while health and built frontend remain accessible.
No data migration, service deployment, secret or DEMO operation is part of this PR.

References: [FastAPI proxy trust](https://fastapi.tiangolo.com/advanced/behind-a-proxy/),
[Starlette middleware](https://www.starlette.dev/middleware/),
[MDN CSP](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy).
