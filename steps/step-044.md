# Day 044 — Complete the 200-case dataset and blind split

> Week 7 · AI quality evaluation · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Finish the brief minimum benchmark dataset before tuning evaluators.

- Add the final 50 unique cases to reach at least 200.
- Create stratified development/calibration, validation, and blind-test splits.
- Hash and lock the split manifest before threshold tuning.

**Deliverable:** a locked 200-case human-labeled benchmark dataset

## 2. Why this matters

Tuning and reporting on the same examples inflates apparent quality; a locked blind split makes the final report credible.

## 3. Previous work done

**Immediate dependencies:** [Day 043](step-043.md).

Before starting, all 43 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 43 earlier steps</summary>

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
- [ ] [Day 037](step-037.md) — detected outages, cost/token spikes, and abnormal API usage
- [ ] [Day 038](step-038.md) — added config/prompt regression comparison and activation evidence
- [ ] [Day 039](step-039.md) — implemented transparent weighted risk scoring with provisional coverage
- [ ] [Day 040](step-040.md) — created deduplicated incidents with evidence, transitions, and audit
- [ ] [Day 041](step-041.md) — added incident APIs and dashboard investigation/acknowledgment
- [ ] [Day 042](step-042.md) — verified detection pipeline and expanded the dataset to 100 cases
- [ ] [Day 043](step-043.md) — defined quality rubric/evaluation contracts and reached 150 cases

</details>

## 4. How to build it

- A practical split is 60 development, 40 validation, and 100 blind test, adjusted transparently for label balance.
- Stratify by failure type, difficulty, and context availability; avoid near-duplicate leakage across splits.
- Record case IDs and file hashes in a manifest; do not inspect blind labels while choosing thresholds.
- Include normal cases as negatives so false-positive rate is measurable.
- Validate licenses/provenance and ensure no real secrets or personal data appear in fixtures.

## 5. Workflow for today

1. Label the final 50 cases and review duplicates.
2. Run stratification and assign splits deterministically.
3. Lock the manifest and dataset hash.
4. Check split balance and leakage.
5. Record the exact 200-case count and any limitations before calibration.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/unit/test_quality_dataset.py backend/tests/unit/test_quality_split.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] At least 200 unique cases exist: 40 + 60 + 50 + 50.
- [ ] The blind split is locked before evaluator tuning.
- [ ] Normal and degraded cases support precision/recall/FPR measurement.
- [ ] Provenance, privacy, and near-duplicate checks pass.

## 8. Done and handoff

Complete the deliverable: **a locked 200-case human-labeled benchmark dataset**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-044: completed 200 quality cases and locked a blind evaluation split`

Next: [Day 045](step-045.md) — Extract bounded quality features without storing content.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
