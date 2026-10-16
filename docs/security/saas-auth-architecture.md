# V13.2 security architecture (mini-RFC)

Status: partial implementation; SaaS activation blocked. Baseline `6f0949676e33a21c640b2ccc2f5877368a4dd0cb`.

## Current boundaries

The composition root (`src/stockpilot/main.py`) builds one Mongo database, one set of repositories, one workflow and one pgvector store. A signed, expiring HS256 `customer_id` protects `/assistant` and customer order read/cancel paths. It represents an order owner in this installation, not an organization. Assistant checkpoints use a hash of `[customer_id, thread_id]`. Products, stock, imports, movements and knowledge have no tenant key. Technical backoffice bearer credentials have global privilege; admin cookies are a separate local operator identity.

## Decisions before implementation

1. Preserve customer ownership and every ORDER guard. Do not reinterpret `customer_id` as a tenant and do not assign a default tenant to historical data.
2. Protect catalogue and diagnostic LLM APIs with the existing customer dependency for controlled local usage. `/classify` has no frontend caller; retain its authenticated compatibility API for eval/tooling, and keep it unavailable in SaaS. Minimal `/health` stays public.
3. Explicit environments: development, test, local_demo, production_saas. The legacy `production` spelling is treated as production_saas. Production SaaS is unconditionally refused by this version, with no enable/bypass flag. Authentication alone cannot safely expose shared stores.
4. Recommend standard OIDC authorization code + PKCE and an application HttpOnly session. Provider, organization membership source, commercial roles and account lifecycle are product decisions. Do not implement a token issuer, shared static SaaS token or fake login.
5. Add independent input/HTTP resource controls and persistent local operator sessions using existing stores. These are useful local hardening; they do not establish SaaS readiness. Distributed quotas and deployment capacity remain explicit activation prerequisites.

## Proposed tenant contract (not active)

A verified principal must contain issuer+subject, an active server-verified tenant membership, permissions and an expiry/revocation reference. `customer_id` remains the buyer identity inside a tenant. Ignore tenant fields/headers from callers. Scope all use cases, repositories, transactions, exact SKU resolution, operation replay payloads, imports, movements and RAG by tenant; no global lookup followed only by an HTTP ownership check.

Every document gains an explicit tenant key, indexes become compound where identities are tenant-local, and atomic stock reservation includes the tenant in both predicate and transactional order. Checkpoint namespace must include tenant, subject and client thread identifier; context/proof/replay verification must include tenant without weakening existing disarming. A shared conversation feature requires a separate authorization decision.

Historical data classification, tenant ownership mapping, duplicate SKU/operation policy and checkpoint conversion need explicit approval. Migration plan: inventory a read-only copy, reject missing/ambiguous mappings, back up, dry-run counts/uniqueness, copy into new scoped stores, validate ORDER/stock/proof/checkpoint invariants, then explicitly schedule a cutover. Rollback keeps original stores/checkpoints read-only and restores the paired snapshot; never merge divergent order/stock state automatically. No automatic migration or historical write is part of V13.2.

## Scope and release sequence

Independent PRs target main: API boundary/configuration (draft, SaaS blocked), input limits, persistent local operator sessions, HTTP boundary if justified. Tenant isolation is a design/backlog dependency, not a claim of implemented multi-tenancy. No merge, release, deploy or v017 change is authorized. Each implementation records its validation and limits.

## Sources

Use the existing PyJWT validator; pin accepted algorithms and check issuer/audience when an identity contract exists ([PyJWT](https://pyjwt.readthedocs.io/en/2.10.1/usage.html)). Server-side opaque sessions retain revocation on logout ([OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)).
