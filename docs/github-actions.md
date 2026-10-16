# GitHub Actions quality gates

[Back to README](../README.md) · [AI evals](ai-evals.md) · [Self-hosted runner](self-hosted-runner.md)

`main` is the stable/demo branch; `dev` is the development branch.
Workflow definitions exist in the repository. Runner registration, environment
permissions/approval, dispatch and artifact delivery must be verified on GitHub;
this guide does not claim that installation or approvals are configured.

## Fast CI: PRs and pushes

[.github/workflows/ci.yml](../.github/workflows/ci.yml) triggers on pull requests
and pushes to **main/dev**, with read-only contents permission. It uses
`ubuntu-latest`, Python 3.12, pip caching and per-job 10-minute timeouts.
Concurrency cancels superseded runs for the same workflow/ref.

| Check | Commands |
|---|---|
| Unit Tests | `python -m pytest -q` (default selection, integration excluded) |
| AI Eval Framework | `python -m pytest -q tests/test_evals.py tests/test_eval_ci.py`, then enriched deterministic smoke |

Both install `requirements-dev.lock` then `python -m pip install --no-deps -e .`.
The 36 eval-framework test cases run in both jobs by design (the second job is the
named AI Eval Framework check). Neither job needs Ollama/PostgreSQL/Mongo. The
frontend, integration and container jobs are documented in [CI/CD](ci-cd.md). The deterministic smoke
validates the framework, **not AI quality**. Branch protection must explicitly
require these checks if merges should be blocked.

## Manual real AI gate

[.github/workflows/ai-evals.yml](../.github/workflows/ai-evals.yml) has
**workflow_dispatch only**, no schedule/push/PR trigger.
**Real AI Quality Gate**:

- Runs only in `WaelOuni/stockpilot-ai` on `refs/heads/dev`.
- Uses `[self-hosted, linux, x64, stockpilot-evals]`, Python 3.12.
- Has a 45-minute timeout, read-only contents permission and concurrency group
  `stockpilot-real-evals` without canceling an in-progress run.
- Uses environment `stockpilot-evals`, explicitly checks out `dev`, and disables
  persisted Git checkout credentials.
- Creates a per-run isolated Python venv and installs the project/dev extras.
- Runs preflight, then historical 60 cases in local mode against the reviewed
  baseline, then always attempts summary/report upload.
- Sets `OTEL_ENABLED=false`, `qwen2.5:7b`, `nomic-embed-text`.
- Takes `POSTGRES_URL` from environment secret `EVAL_POSTGRES_URL` and Ollama URL
  from environment variable `EVAL_OLLAMA_BASE_URL` (default localhost:11434).

It requires installed `nomic-embed-text:latest`/Qwen models and a dedicated,
already ingested pgvector eval DB matching `data/products` exactly.
CI deliberately does not download models or ingest/schema-initialize the DB.
Never target production or a mutable live corpus. A read-only eval account is
preferred; provisioning/ingestion requires separate write access.

## Environment security and execution

Configure repository **Settings -> Environments -> stockpilot-evals**:

1. Restrict deployment branches to **dev only**.
2. Require independent trusted reviewer approval; prevent self-approval where
   available. Confirm these protections are supported/enforced for the repo.
3. Add secret `EVAL_POSTGRES_URL` for the runner-reachable dedicated DB.
4. Optionally set variable `EVAL_OLLAMA_BASE_URL` for the actual workflow-process
   network path (see [runner networking](self-hosted-runner.md#wsl-and-windows-host-ollama)).
5. Protect `dev` and workflow changes; retain repository/dev guard, explicit
   checkout and disabled credential persistence.

The job executes repository Python with local infrastructure/credentials.
Use isolated, trusted dev infrastructure and never run untrusted PR code.
A YAML guard cannot prevent authorized workflow editors from removing it.
Review permissions and runner isolation, not just labels.

Run **Actions -> StockPilot Real AI Evals -> Run workflow**, select **dev**,
then obtain independent environment approval if configured.
Without a matching runner the job queues; it does not silently fall back to
synthetic or GitHub-hosted model execution. Hosted runners are not assumed to
provide predictable Qwen CPU/RAM/time or repeated multi-GB downloads.

## Preflight, failures and reports

Preflight validates connectivity, installed models, exact corpus content and
embedding/vector-search compatibility. Environment failure is explicit and
does not fabricate 0% scores. The gate fails for regression beyond baseline
tolerance (currently 3 percentage points), incompatible configuration,
incomplete evaluation or execution failure. Individual misses alone do not
fail an otherwise completed, non-regressing run.

Summary and upload steps use `always()`. Artifact
`stockpilot-ai-eval-results-<run_id>-<attempt>` has **14-day retention**.
Step Summary includes scores/regressions, hashes, baseline SHA256 and model names.
Failed-preflight JSON has metadata but no scores. Failures before report
generation may leave no artifact; `always()` does not guarantee a file exists.

**CI never updates `evals/baseline.json`.** Baseline changes require separate
human review of provenance/failures/measured scores. Older product-extraction
regressions are documented measurement history, not proof of a current failure;
never weaken the reference to hide a newly measured regression.

## Local equivalents and validation

From the project root with a dedicated eval URL and reachable models:

```sh
python -m pytest -q
python scripts/run_evals.py --mode deterministic --suite enriched
# Configure eval URL securely; keep real credentials out of history/logs:
python scripts/eval_ci.py preflight --output eval-results.json
python scripts/run_evals.py --mode local --compare-baseline --output eval-results.json
python scripts/eval_ci.py summary --report eval-results.json
python -m pytest -q tests/test_evals.py tests/test_eval_ci.py
```

Local tests verify YAML/helper behavior, not GitHub environment permissions,
runner service configuration, approval or actual artifact delivery.
Verify those separately in GitHub after setup; do not assume them from a push.
