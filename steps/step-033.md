# Day 033 — Visualize latency errors tokens and cost

> Week 5 · Metrics, cost, and monitoring dashboard · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Build the first useful monitoring overview from real backend rollups.

- Add Recharts panels for request volume, p50/p95/p99 latency, error rate, tokens, and estimated cost.
- Add application/provider/time-range filters and freshness indicators.
- Show uncertainty and empty/stale states clearly.

**Deliverable:** a browser-verified monitoring overview with honest units and freshness

## 2. Why this matters

The dashboard should help an operator identify change quickly, not merely display attractive charts without units or provenance.

## 3. Previous work done

**Immediate dependencies:** [Day 029](step-029.md), [Day 030](step-030.md), [Day 031](step-031.md), [Day 032](step-032.md).

Before starting, all 32 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 32 earlier steps</summary>

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
- [ ] [Day 026](step-026.md) — exposed bounded metrics and provisioned Prometheus/Grafana dashboards
- [ ] [Day 027](step-027.md) — tested queue outages, redelivery, dead letters, and bounded backpressure
- [ ] [Day 028](step-028.md) — verified end-to-end event accounting and metadata-only telemetry
- [ ] [Day 029](step-029.md) — built idempotent latency/error/token window aggregates
- [ ] [Day 030](step-030.md) — added versioned pricing, estimated costs, and budget reconciliation
- [ ] [Day 031](step-031.md) — implemented request/overview/latency/cost APIs with cursor pagination
- [ ] [Day 032](step-032.md) — scaffolded React dashboard with secure sessions and typed API access

</details>

## 4. How to build it

- Use API-provided aggregates; do not recompute percentiles by averaging request values in the browser.
- Label units, time zone, estimate status, and rollup last update time.
- Keep colors accessible and provide text/table equivalents for important values.
- Debounce filter changes and cancel stale requests to avoid out-of-order rendering.
- Seed deterministic synthetic traffic for visual verification.

## 5. Workflow for today

1. Design the overview layout around the brief operational questions.
2. Implement typed hooks and chart components.
3. Generate synthetic normal/degraded traffic.
4. Open the dashboard in a browser and exercise filters, empty data, stale data, and errors.
5. Inspect console/network behavior and run frontend checks.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
npm --prefix frontend run test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run dev -- --host 127.0.0.1
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Charts match backend aggregate values and units.
- [ ] Estimated/unknown cost and stale rollups are visibly labeled.
- [ ] Filters cannot display another organization data.
- [ ] Golden and edge states pass in a real browser with no unhandled console errors.

## 8. Done and handoff

Complete the deliverable: **a browser-verified monitoring overview with honest units and freshness**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-033: visualized latency, errors, tokens, traffic, and estimated cost`

Next: [Day 034](step-034.md) — Add request history and metadata drill-down.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
