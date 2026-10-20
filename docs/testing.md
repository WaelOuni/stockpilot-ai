# Testing

Never point tests at the development stores or at a DEMO. Integration fixtures refuse
any target that is not an explicitly configured, dedicated test service.

## Backend (Python 3.12)

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

The default selection excludes tests marked `integration` (`pyproject.toml`). Mocked
tests cover the ORDER guards, quantities, confirmation, checkpoints logic,
customer ownership, authentication and CSRF.

## Integration (real MongoDB replica set and PostgreSQL)

Start the disposable services under their own Compose project name, export the two
test URIs they publish, verify them, then select by marker:

```sh
docker compose -f docker/compose.ci.yml -p stockpilot-local-tests up -d --wait
export MONGO_TEST_URI='mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true'
export MONGO_MIGRATION_AUTH_TEST_URI='mongodb://ci:ci@127.0.0.1:27028/?authSource=admin'
export POSTGRES_TEST_URI='postgresql://ci:ci@127.0.0.1:55432/stockpilot_ci'
.venv/bin/python scripts/ci/check_targets.py
.venv/bin/python -m pytest -q -m "integration and not integration_optional" tests
```

Without both variables, or with an application endpoint (default ports 27017/5432,
`Settings` endpoints, `stockpilot*` databases), the fixtures fail before writing.
`integration_optional` marks the demo lifecycle test, which needs its own dedicated
services (`scripts/tests/demo-compose.yml`) and skips otherwise. Remove only the
containers of your own Compose project when finished.

## Frontend

```sh
cd frontend && npm ci && npm test && npm run typecheck && npm run lint && npm run build
```

V13.C1 has a separate fully synthetic browser harness (no database/provider clients):

```sh
cd frontend && npx playwright test --config playwright.advice-synthetic.config.ts
```

It starts its own API/Vite on local ports 8002/5174, refuses existing servers and
uses an isolated dotenv directory and generated test JWT key. See
[catalogue-advice](catalogue-advice.md) for its contract and limitations.

Other Playwright E2E specs are described in [frontend/README](../frontend/README.md); the live
ones need real Ollama and are opt-in.

The historical live initializer additionally requires explicit `E2E_MONGO_URI`
and `E2E_POSTGRES_URL` for the tmpfs-only `stockpilot-e2e-tests` project in
`docker/compose.e2e.yml`. It inspects actual Docker labels, Compose provenance,
exclusive network, published loopback ports and mounts before any DB creation or
reset. A matching database name on a development/DEMO server is insufficient.
Remote Docker hosts/contexts are refused; the effective daemon must use a local
Unix socket so inspected published ports describe the same loopback as clients.
See the frontend runbook for exact commands. The dedicated synthetic browser
workflow runs the Advice harness above on relevant `main` PRs, with a 12-minute
ceiling; it does not activate the historical real-model suite or modify required
CI/release jobs. Real Ollama validation remains separate from deterministic proof.

## AI evaluations

```sh
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
```

The deterministic suite validates the framework, not model quality. Real evals are a
manual gate, see [ai-evals](ai-evals.md) and [github-actions](github-actions.md).
