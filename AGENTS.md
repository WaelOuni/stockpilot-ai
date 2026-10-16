# StockPilot AI: agent instructions

Keep this file short; details live in `docs/`.

## Layout
- `src/stockpilot/`: domain, application (use cases, workflow nodes), ports, infrastructure adapters (MongoDB, PostgreSQL/pgvector, Ollama), presentation (FastAPI).
- `frontend/`: Vue 3 + Vite. `tests/`: pytest. `evals/`: AI eval datasets. `scripts/`: tooling and CI guards.
- `main` is stable and releases come from it. `dev` holds independent V11 work: never merge or cherry-pick it into stable work.

## Validate before a PR
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`
- `.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched`
- Integration tests: see `docs/testing.md` (disposable services only).
- Frontend: `cd frontend && npm test && npm run typecheck && npm run lint && npm run build`
- `git diff --check`; never commit secrets, `.env*` (except `.env.example`), logs or artifacts.

## Hard rules
- Tests must never use application or DEMO databases. Integration fixtures need explicit `MONGO_TEST_URI` / `POSTGRES_TEST_URI` for dedicated services and refuse anything else.
- Do not modify DEMO deployments under `~/deployments/`, their volumes, secrets or ports without explicit authorization. No `docker system/volume/network/image prune`, no `compose down -v` outside a project you created.
- Do not weaken or delete regression tests because their name mentions an old version; prove equivalent coverage first.
- ORDER guarantees to preserve: lexical/negative/fractional quantity rejection, exact SKU/order_id identity, explicit confirmation with a persisted proof and disarming, idempotent operation ids, atomic stock reservation, customer ownership isolation, checkpoint persistence.
- Do not change ORDER logic, prompts or models without a separate, explicit request.

## Workflow
- Use a dedicated git worktree per task; keep unrelated worktrees untouched.
- Open PRs against `main` explicitly (the repository default branch is `dev`). Do not merge, tag or release without instruction.
- Each deletion needs evidence (no references, dynamic usages checked) and a non-regression check.