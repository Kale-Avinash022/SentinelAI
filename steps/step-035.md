# Day 035 — Verify the monitoring MVP and label first cases

> Week 5 · Metrics, cost, and monitoring dashboard · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Checkpoint the first monitoring product and begin spreading the 200-case labeling workload.

- Verify API-to-worker-to-rollup-to-dashboard values with deterministic traffic.
- Create a draft labeling guide and the first 40 synthetic quality cases with stable IDs.
- Close Week 5 gaps before risk detection.

**Deliverable:** a verified monitoring MVP checkpoint and 40 draft quality cases

## 2. Why this matters

The brief requires a working monitoring dashboard before detection/recovery, and 200 labels are too much work for one later day.

## 3. Previous work done

**Immediate dependencies:** [Day 029](step-029.md), [Day 030](step-030.md), [Day 031](step-031.md), [Day 032](step-032.md), [Day 033](step-033.md), [Day 034](step-034.md).

Before starting, all 34 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 34 earlier steps</summary>

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
- [ ] [Day 033](step-033.md) — visualized latency, errors, tokens, traffic, and estimated cost
- [ ] [Day 034](step-034.md) — added metadata-only request history, filters, and trace drill-down

</details>

## 4. How to build it

- Compare a known synthetic event set against API totals and visible chart values.
- Record screenshots/test output with environment and commit, not fabricated metrics.
- Store only synthetic or properly licensed public examples under benchmarks/quality; no private prompts.
- Draft labels for groundedness/relevance/completeness/refusal/safety/structured validity, marking uncertain cases for review.
- Keep the first 40 provisional until Day 43 finalizes the rubric and schema.

## 5. Workflow for today

1. Run backend/frontend checks and a synthetic traffic fixture.
2. Reconcile dashboard values with the fixture.
3. Manually verify filters, drill-down, cost labels, and empty states in a browser.
4. Write the labeling-guide draft and label 40 cases.
5. Record checkpoint evidence and carry uncertainties into Week 7.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests -m "not real_provider" -q
npm --prefix frontend run test -- --run
npm --prefix frontend run build
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Dashboard totals match the deterministic fixture.
- [ ] Week 5 browser and API checks pass.
- [ ] Exactly 40 stable-ID cases are recorded, bringing the cumulative draft total to 40.
- [ ] No private prompt/response content enters the benchmark dataset.

## 8. Done and handoff

Complete the deliverable: **a verified monitoring MVP checkpoint and 40 draft quality cases**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-035: verified monitoring MVP and created the first 40 quality cases`

Next: [Day 036](step-036.md) — Build the detector framework and latency checks.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
