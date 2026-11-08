# Day 004 — Start PostgreSQL and Redis with Docker Compose

> Week 1 · Foundation and architecture · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Provide reproducible local dependencies with safe persistence and separate database identities.

- Create a core Compose profile for PostgreSQL and Redis.
- Use named volumes, health checks, localhost-only ports, and resource settings.
- Provision a migration owner and a non-owner application database role.

**Deliverable:** a healthy local core stack with persistent volumes and separate DB roles

## 2. Why this matters

Local infrastructure should behave like a testable dependency, while least-privilege database roles make later RLS enforcement meaningful.

## 3. Previous work done

**Immediate dependencies:** [Day 002](step-002.md), [Day 003](step-003.md).

Before starting, all 3 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 3 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test
- [ ] [Day 003](step-003.md) — added repeatable lint, type, test, dependency, and secret checks

</details>

## 4. How to build it

- Keep docker-compose.yml at the root and use profiles so observability/AWS-local/Kafka services do not all start by default.
- Store PostgreSQL and Redis data in named Docker volumes, not OneDrive bind mounts.
- Initialize sentinel_migrator and sentinel_app through a local-only bootstrap script; sentinel_app has no ownership, SUPERUSER, or BYPASSRLS.
- Keep development credentials in ignored .env and use placeholders in examples; never reuse them in AWS.
- Add health checks and document that stopping containers preserves volumes; deleting volumes is a separate destructive decision.

## 5. Workflow for today

1. Confirm Docker Desktop is running and ports are free.
2. Add the core services and local bootstrap configuration.
3. Start only the core profile and inspect health.
4. Connect as each role and verify privileges.
5. Restart the stack and confirm data survives without deleting volumes.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
docker compose config
docker compose --profile core up -d
docker compose ps
docker compose --profile core logs --tail=100
docker compose restart
docker compose ps
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] PostgreSQL and Redis become healthy and are not exposed beyond localhost.
- [ ] Data survives a normal restart.
- [ ] The application role is non-owner and lacks BYPASSRLS.
- [ ] Compose validation passes and no real credential is committed.

## 8. Done and handoff

Complete the deliverable: **a healthy local core stack with persistent volumes and separate DB roles**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-004: started local PostgreSQL/Redis with health checks and least-privilege roles`

Next: [Day 005](step-005.md) — Connect SQLAlchemy and create the Alembic baseline.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
