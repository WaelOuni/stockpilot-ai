# Self-hosted real-eval runner

[Back to README](../README.md) · [GitHub Actions](github-actions.md) · [Eval database](database.md#dedicated-eval-database)

This is a **manual setup guide**, not evidence that a runner, service, network
binding or GitHub approval policy is already configured. Do not execute setup
or modify services/firewalls without infrastructure-owner approval.

## Trusted prerequisites

Use an isolated, trusted Linux x64 development host/WSL distro with Python 3.12,
git and sufficient RAM/disk for Qwen. Do not expose the runner to untrusted PR
code: jobs execute checked-out Python with infrastructure/database credentials.
Runner labels select machines; they are not a security boundary.

Provision outside CI:

- Ollama reachable by the **workflow process**, with `qwen2.5:7b` and
  `nomic-embed-text:latest` installed.
- Dedicated PostgreSQL/pgvector eval DB with the exact three current
  `data/products` documents; use [safe initialization](database.md#dedicated-eval-database)
  if `stockpilot_eval` already exists.
- Separate read-only eval database credentials where possible; use write-capable
  provisioning credentials only for schema/ingestion.
- Trusted **dev-only** GitHub environment with independent approval and protected
  workflow changes, as described in [GitHub Actions](github-actions.md).

Do not point CI at production, mutable live inventory/corpus or valuable data.
Docker access is highly privileged; provide it only if required for provisioning,
not because real evaluation itself needs to launch containers.

## Download and register Linux x64

In `WaelOuni/stockpilot-ai`, open **Settings -> Actions -> Runners ->
New self-hosted runner** and select **Linux**, **x64**.
Use GitHub's **current generated** download URL, archive name and verification
command; do not copy an invented version/checksum from a guide.

Keep the runner **outside the repository** in WSL/Linux:

```text
~/projects/stockpilot-ai/   project
~/actions-runner/          runner, configuration and _work checkouts
```

The existing local runner has been moved to `~/actions-runner/` without
reconfiguration. To start that registered runner, use only:

```sh
cd ~/actions-runner
./run.sh
```

The download/registration commands below are for a **new installation only**.
Do not rerun `config.sh` or extract an archive over the existing runner.
The repository retains `actions-runner/` in `.gitignore` as an extra safeguard,
but ignoring a directory does not prevent pytest from discovering files in it.

```sh
mkdir -p ~/actions-runner
cd ~/actions-runner
# Replace placeholders with the current values shown by GitHub:
curl -fL -o RUNNER_ARCHIVE.tar.gz 'GITHUB_PROVIDED_LINUX_X64_DOWNLOAD_URL'
# Run GitHub's generated archive verification command before extracting.
tar xzf RUNNER_ARCHIVE.tar.gz
./config.sh --url https://github.com/WaelOuni/stockpilot-ai \
  --token REGISTRATION_TOKEN --labels stockpilot-evals
./run.sh
```

`REGISTRATION_TOKEN` is a placeholder for the short-lived registration token
from GitHub, **not** a PAT. Never commit it, paste it into logs/reports or share
it; use approved secure handling and avoid retaining it in shell history.
GitHub's installer/configuration prompts supply the runner name/work directory.
Do not run the runner as root. Keep `run.sh` foreground for the initial test;
stopping it with Ctrl+C makes the runner unavailable.

Verify in GitHub that it is **Idle/online**, registered to the intended repository,
and has default `self-hosted`, `linux`, `x64` plus custom `stockpilot-evals`
labels. Confirm the intended OS/architecture rather than assuming label spelling.
The job queues until an eligible runner and required environment approval exist.

All evaluation commands elsewhere run from the project root, not the runner
installation directory. The workflow checks out the repository into its own
workspace and creates its own Python venv; an interactive project venv is not
proof of the workflow environment.

Keep `_work` outside the project along with the runner. A nested checkout was
previously responsible for 29 pytest collection errors from duplicate test
modules; moving the runner resolved this without deleting `_work` or adding
pytest exclusions. The normal project command now works directly:

```sh
cd ~/projects/stockpilot-ai
source .venv/bin/activate
python -m pytest -q
```

The post-move validation returned **240 passed, 0 failed, 0 errors,
4 deselected**. If relocating an existing runner again, first stop its process
cleanly and ensure it has no active job. Preserve the complete directory,
including hidden configuration/credential files, `_diag` and `_work`.
Do not print credential contents or re-register a runner that reconnects
successfully. An installed system service also needs its old paths reviewed;
the relocated local runner currently uses `./run.sh`, not a system service.

## Optional system service

Only after approval and a successful foreground run, GitHub's Linux runner
supports service installation from its installation directory:

```sh
cd ~/actions-runner
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status
```

This requires systemd. WSL may not have systemd enabled; do not assume `svc.sh`
works or silently change WSL/system settings. Keep the foreground runner or
obtain approval for the supported service approach. Service users/environments
may differ from the interactive shell. Revalidate connectivity/permissions in
that context. This guide does not install or leave a background service running.

## WSL and Windows-host Ollama

The workflow defaults to `http://localhost:11434`. That works only when Ollama
is reachable there **from the runner job**. Interactive shell exports do not
prove reachability in a runner service or survive the workflow's explicit
`OLLAMA_BASE_URL` setting.

From the project root, the existing dynamic-gateway helper can discover a
candidate Windows-host URL without hardcoding a WSL IP:

```sh
source scripts/dev-env.sh
curl "$OLLAMA_BASE_URL/api/tags"
```

The helper uses `ip route`/`awk` and needs iproute2; it also activates the project
venv and sets model names. It does not ensure Ollama listens on the gateway,
configure Windows firewall or work for every WSL networking mode.
Keep listeners/firewalls restricted to the trusted runner environment.

Configure **environment variable** `EVAL_OLLAMA_BASE_URL` in GitHub environment
`stockpilot-evals` to the reachable URL. The workflow assigns that value to
`OLLAMA_BASE_URL`; exporting it only in an interactive shell is insufficient.
Gateway addresses can change after WSL restart: rediscover/revalidate and update
the environment variable as needed, or use a stable host arrangement approved
for that environment. Do not paste a fixed example IP into the workflow.

Similarly, `EVAL_POSTGRES_URL` must use a host reachable from the runner workflow.
The public local-demo URL
`postgresql://stockpilot:stockpilot@localhost:5432/stockpilot_eval` is appropriate
**only** for that local demo topology, not dedicated production credentials.
Changing runner host/container/network changes what localhost means.

## Verify the real job

### Runner states and connection troubleshooting

**Offline** means the runner process/service is not connected to GitHub.
**Idle** means it is connected and ready; **Active** means it is executing a job.
A queued job can instead be waiting for labels, environment approval or the
workflow concurrency slot. Check these separately before restarting services.

For an Ollama `ConnectError`, test the network path from the runner's actual
user/environment, not just another interactive terminal:

```sh
# Substitute the runner-reachable value configured as EVAL_OLLAMA_BASE_URL:
curl --fail --connect-timeout 5 "$OLLAMA_BASE_URL/api/tags"
```

Confirm Ollama is running, the URL matches its listener, and the trusted
Windows/WSL firewall path permits the connection. Under WSL NAT, localhost may
not reach Windows Ollama; use the gateway helper above to discover a candidate
address, then set the GitHub environment variable and retest. Do not solve this
by exposing Ollama publicly. A reachable server with missing models, or an
unreachable PostgreSQL/exact-corpus mismatch, is a different preflight failure;
use the preflight report to identify which dependency needs attention.

Select **dev** under **Actions -> StockPilot Real AI Evals -> Run workflow**.
Obtain independent environment approval, confirm the intended runner executes
the job, and inspect preflight plus the historical baseline comparison.
The workflow has a 45-minute timeout, read-only contents permission, explicit
dev checkout and disabled credential persistence. It downloads neither models
nor corpus, never updates the baseline, and always attempts a 14-day report upload.

Check the Step Summary and JSON artifact. Environment failures have no fabricated
quality scores; failures before report creation may have no artifact.
Do not weaken the baseline, broaden trusted branches, expose a database or disable
approval merely to make a queued/failed run green. See
[GitHub Actions](github-actions.md) for exact triggers and report semantics.
