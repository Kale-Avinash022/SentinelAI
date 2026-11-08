# Day 026 — Expose Prometheus metrics and provision Grafana

> Week 4 · Asynchronous telemetry and observability · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Make operational health measurable with bounded labels and reproducible dashboards.

- Expose request rate/error/duration, token/cost estimate, outbox age, worker lag, and dependency health metrics.
- Add Prometheus scrape configuration and rule validation.
- Provision Grafana datasources and a minimal infrastructure dashboard.

**Deliverable:** bounded Prometheus metrics and provisioned Grafana monitoring

## 2. Why this matters

Metrics answer aggregate operational questions; traces investigate individual requests. Confusing the two creates expensive high-cardinality storage.

## 3. Previous work done

**Immediate dependencies:** [Day 019](step-019.md), [Day 020](step-020.md), [Day 022](step-022.md), [Day 023](step-023.md), [Day 024](step-024.md), [Day 025](step-025.md).

Before starting, all 25 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 25 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test
- [ ] [Day 003](step-003.md) — added repeatable lint, type, test, dependency, and secret checks
- [ ] [Day 004](step-004.md) — started local PostgreSQL/Redis with health checks and least-privilege roles
- [ ] [Day 005](step-005.md) — connected async SQLAlchemy and verified migration upgrade/downgrade behavior
- [ ] [Day 006](step-006.md) — built FastAPI app factory, versioned API, health checks, and safe errors
- [ ] [Day 007](step-007.md) — verified clean-setup bootstrap and closed foundation gaps
- [ ] [Day 008](step-008.md) — defined tenant models, provider catalog, and four-role permission matrix
- [ ] [Day 009](step-009.md) — applied constrained tenant schema, indexes, and least-privilege grants
- [ ] [Day 010](step-010.md) — enforced forced RLS with transaction-local tenant context
- [ ] [Day 011](step-011.md) — proved tenant isolation across APIs, writes, and pooled sessions
- [ ] [Day 012](step-012.md) — added secure login, short JWT sessions, refresh rotation, and RBAC
- [ ] [Day 013](step-013.md) — implemented application/key lifecycle, hashed credentials, and append-only audit
- [ ] [Day 014](step-014.md) — added immutable config/prompt versions, redacted diffs, and CAS activation
- [ ] [Day 015](step-015.md) — defined chat contracts and deterministic multi-failure fake providers
- [ ] [Day 016](step-016.md) — integrated LiteLLM behind the port with local and gated real adapters
- [ ] [Day 017](step-017.md) — added durable provider attempts and scoped idempotency claims
- [ ] [Day 018](step-018.md) — handled unknown paid outcomes without unsafe automatic replay
- [ ] [Day 019](step-019.md) — built snapshot-based routing with capability, privacy, and budget checks
- [ ] [Day 020](step-020.md) — added SSE streaming, cancellation accounting, and tenant rate limits
- [ ] [Day 021](step-021.md) — verified authenticated gateway contracts, budgets, streaming, and idempotency
- [ ] [Day 022](step-022.md) — added metadata-only events and transactional outbox persistence
- [ ] [Day 023](step-023.md) — connected the outbox to one active Celery/Redis transport
- [ ] [Day 024](step-024.md) — made worker effects idempotent with bounded retries and dead letters
- [ ] [Day 025](step-025.md) — added correlated OpenTelemetry traces across API, queue, and workers

</details>

## 4. How to build it

- Use histograms for latency/queue delay and counters for requests/errors/tokens; keep labels bounded and avoid raw model strings supplied by users.
- Separate gateway overhead from upstream generation duration.
- Protect or network-isolate /metrics; do not expose tenant-sensitive operational detail publicly.
- Provision Grafana through files rather than manual clicks; use local anonymous read-only access only in the lab profile.
- Add promtool checks and a scrape smoke test to CI/local scripts.

## 5. Workflow for today

1. Define metric names, units, labels, and SLI meanings.
2. Instrument gateway/relay/worker paths.
3. Add Prometheus/Grafana configuration under monitoring.
4. Generate synthetic traffic and verify series/dashboard panels.
5. Run a cardinality review and rule validation.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
docker compose --profile observability up -d
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml
.venv/Scripts/python -m pytest backend/tests/integration/test_metrics.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Prometheus scrapes the application and worker metrics successfully.
- [ ] Latency histograms, queue age, errors, tokens, and cost estimates are present.
- [ ] Labels exclude request IDs, user IDs, prompts, and unbounded model input.
- [ ] Grafana provisioning reproduces the dashboard without manual setup.

## 8. Done and handoff

Complete the deliverable: **bounded Prometheus metrics and provisioned Grafana monitoring**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-026: exposed bounded metrics and provisioned Prometheus/Grafana dashboards`

Next: [Day 027](step-027.md) — Test queue failures redelivery and backpressure.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
