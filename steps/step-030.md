# Day 030 — Calculate costs with versioned pricing

> Week 5 · Metrics, cost, and monitoring dashboard · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Estimate request/application/model/organization spending without hardcoding vendor rates.

- Add effective-dated pricing versions by provider/model/token type and currency.
- Compute estimated costs from usage and reconcile budget reservations.
- Separate failed-request, retry, evaluation, and unknown-cost accounting.

**Deliverable:** versioned cost estimation with uncertainty and budget reconciliation

## 2. Why this matters

Token prices change and provider usage may be estimated; a trustworthy cost dashboard exposes uncertainty and pricing provenance.

## 3. Previous work done

**Immediate dependencies:** [Day 014](step-014.md), [Day 017](step-017.md), [Day 019](step-019.md), [Day 024](step-024.md), [Day 029](step-029.md).

Before starting, all 29 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 29 earlier steps</summary>

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

</details>

## 4. How to build it

- Use Decimal arithmetic and explicit units such as price per million tokens; avoid float accumulation for money.
- Select the price version effective at request time and persist its identifier.
- Label all derived values estimated unless reconciled to authoritative billing evidence.
- Reconcile reserved maximum cost against actual/unknown usage without releasing funds for ambiguous attempts prematurely.
- Add daily/monthly rollups and tests for price boundaries, cached tokens if supported, and missing prices.

## 5. Workflow for today

1. Define the pricing schema and import a synthetic/local price fixture.
2. Implement pure cost calculation and version selection.
3. Connect worker cost projection and reservation reconciliation.
4. Test changing prices, unknown usage, failures, retries, and evaluation calls.
5. Expose estimate labels in API schemas for the dashboard.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/unit/test_pricing.py backend/tests/integration/test_cost_accounting.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Cost calculations use Decimal and documented token units.
- [ ] Every estimated cost identifies its pricing version.
- [ ] Unknown usage/pricing remains unknown rather than free.
- [ ] Retry/evaluation/failed-request costs are separately attributable.

## 8. Done and handoff

Complete the deliverable: **versioned cost estimation with uncertainty and budget reconciliation**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-030: added versioned pricing, estimated costs, and budget reconciliation`

Next: [Day 031](step-031.md) — Expose request metrics and cost APIs.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
