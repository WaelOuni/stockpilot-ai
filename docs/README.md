# Documentation index

Start here, then follow the guide for your task. Historical validation reports are
kept in [archive/](archive/README.md) and are not current procedures.

| Task | Read |
|---|---|
| Understand the product and its limits | [README](../README.md) |
| Understand layers, flows and boundaries | [architecture](architecture.md), [order-flow](order-flow.md), [rag](rag.md) |
| Install and run locally | [getting-started](getting-started.md) (uses [.env.example](../.env.example)) |
| Run the persistent local demo | [demo-runbook](demo-runbook.md), [demo-dataset](demo-dataset.md) |
| Run tests | [testing](testing.md), [ai-evals](ai-evals.md), [evals/README](../evals/README.md), [frontend/README](../frontend/README.md) |
| Build, release, restore | [ci-cd](ci-cd.md), [github-actions](github-actions.md), [database](database.md) (migrations, recovery, checkpoints) |
| Operate and observe | [observability](observability.md), [self-hosted-runner](self-hosted-runner.md) |
| SaaS security design | [V13.2 architecture](security/saas-auth-architecture.md), [V14 proposal and incremental plan](security/v14-saas-design.md) (activation blocked; V14 not implemented) |
| Security guarantees | [order-flow](order-flow.md) (authentication, ownership, confirmation proof), [observability](observability.md) (redaction) |
