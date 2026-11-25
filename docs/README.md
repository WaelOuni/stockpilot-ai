# Documentation index

Start with the [local development guide](LOCAL_DEVELOPMENT.md) for the v0.2.9 toolchain.

| Task | Read |
|---|---|
| Operate the isolated B2B demo | [Canonical target and promotion gates](b2b-demo.md) |
| Internal tenant RAG/conversations and PostgreSQL migrations | [ADR B4](security/tenant-rag-conversations.md), [explicit migrations](postgresql-migrations.md) |
| Resume project work and priorities | [Project status](project/PROJECT_STATUS.md), [roadmap](project/ROADMAP.md), [decisions](project/ARCHITECTURE_DECISIONS.md), [agent handoff](project/AGENT_HANDOFF.md) |
| Understand the product and its limits | [README](../README.md) |
| Prepare a commercial demonstration or isolated pilot | [Product readiness](product/README.md) (positioning, demo, onboarding, cost hypotheses and prospect validation) |
| Understand layers, flows and boundaries | [architecture](architecture.md), [order-flow](order-flow.md), [rag](rag.md) |
| Install and run locally | [getting-started](LOCAL_DEVELOPMENT.md) (uses [.env.example](../.env.example)) |
| Run the persistent local demo | [demo-runbook](demo-runbook.md), [demo-dataset](demo-dataset.md) |
| Run tests | [testing](testing.md), [ai-evals](ai-evals.md), [evals/README](../evals/README.md), [frontend/README](../frontend/README.md) |
| Build, release, restore | [ci-cd](ci-cd.md), [github-actions](github-actions.md), [database](database.md) (migrations, recovery, checkpoints) |
| Operate and observe | [observability](observability.md), [self-hosted-runner](self-hosted-runner.md) |
| SaaS security design | [V13.2 architecture](security/saas-auth-architecture.md), [V14 proposal and incremental plan](security/v14-saas-design.md) (activation blocked; V14 not implemented) |
| Security guarantees | [order-flow](order-flow.md) (authentication, ownership, confirmation proof), [observability](observability.md) (redaction) |
