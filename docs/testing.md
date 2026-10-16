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

Playwright E2E specs are described in [frontend/README](../frontend/README.md); the live
ones need real Ollama and are opt-in.

## AI evaluations

```sh
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
```

The deterministic suite validates the framework, not model quality. Real evals are a
manual gate, see [ai-evals](ai-evals.md) and [github-actions](github-actions.md).