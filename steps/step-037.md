# Day 037 — Detect outages cost spikes and traffic anomalies

> Week 6 · Risk detection and incidents · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Implement the brief operational risk categories using transparent deterministic/statistical rules.

- Detect provider outage from error/timeout classes and bounded active health evidence.
- Detect token/cost spikes and abnormal request/key usage.
- Distinguish client errors, throttling, provider failures, and no-traffic states.

**Deliverable:** tested outage, spending, token, and traffic anomaly signals

## 2. Why this matters

A 45% provider error rate means something different from 45% client validation failures; recovery must respond to cause, not a single aggregate.

## 3. Previous work done

**Immediate dependencies:** [Day 018](step-018.md), [Day 019](step-019.md), [Day 020](step-020.md), [Day 029](step-029.md), [Day 030](step-030.md), [Day 036](step-036.md).

Before starting, all 36 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 36 earlier steps</summary>

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
- [ ] [Day 035](step-035.md) — verified monitoring MVP and created the first 40 quality cases
- [ ] [Day 036](step-036.md) — built versioned detector framework with warm-up and hysteresis

</details>

## 4. How to build it

- Use passive windows plus optional allowlisted synthetic health probes with strict frequency/cost caps; no traffic is not proof of outage.
- Compare recent rates to historical baselines with minimum volume and confidence/uncertainty.
- For cost/token anomalies, normalize by request volume and model mix before flagging absolute spend.
- For traffic abuse, examine per-key/application bursts, rejection rates, and unusual token shapes without storing content.
- Emit evidence and proposed next investigation, not direct recovery mutations.

## 5. Workflow for today

1. Define error taxonomy and provider-health states.
2. Implement outage, token/cost, and traffic detectors on shared windows.
3. Build synthetic sequences for normal growth, outage, 429 storm, and cost spike.
4. Tune thresholds against fixtures and record false positives.
5. Verify detectors remain tenant-scoped and produce no side effects.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/unit/test_outage_detector.py backend/tests/unit/test_cost_traffic_detectors.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Provider outage is distinguished from client errors and no traffic.
- [ ] Cost spikes are checked both absolutely and per request/model mix.
- [ ] Active probes are bounded, allowlisted, and fake by default.
- [ ] Traffic anomalies cannot leak or affect another tenant.

## 8. Done and handoff

Complete the deliverable: **tested outage, spending, token, and traffic anomaly signals**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-037: detected outages, cost/token spikes, and abnormal API usage`

Next: [Day 038](step-038.md) — Detect configuration and prompt regressions.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
