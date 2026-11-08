# Day 032 — Scaffold the React TypeScript dashboard

> Week 5 · Metrics, cost, and monitoring dashboard · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Create the control-dashboard foundation with secure session handling and typed API access.

- Initialize React + TypeScript + Vite under frontend.
- Add login/logout, current-user state, route guards, and organization context from the server.
- Generate or maintain typed API contracts and reusable loading/error components.

**Deliverable:** a browser-verified authenticated React/TypeScript shell

## 2. Why this matters

A typed frontend with correct session boundaries prevents duplicated API assumptions and insecure token storage.

## 3. Previous work done

**Immediate dependencies:** [Day 012](step-012.md), [Day 031](step-031.md).

Before starting, all 31 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 31 earlier steps</summary>

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

</details>

## 4. How to build it

- Keep access tokens in memory and use the HttpOnly refresh cookie flow; never store tokens in localStorage.
- Configure a local development proxy or narrow CORS policy with credentials; do not use wildcard origins.
- Use a small routing/state approach already familiar from TypeScript; avoid adding a global state framework without need.
- Handle 401 refresh once, forbidden states, offline errors, and empty data consistently.
- Add npm lockfile, lint/typecheck/build/test scripts, and component tests.

## 5. Workflow for today

1. Scaffold the frontend and commit its lockfile.
2. Implement the typed API client and auth flow.
3. Add a minimal authenticated shell with navigation placeholders only for implemented pages.
4. Start backend and frontend, then use the feature in a browser.
5. Check login, refresh, logout, forbidden access, console errors, and network requests.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test -- --run
npm --prefix frontend run dev -- --host 127.0.0.1
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Login/refresh/logout work in an actual browser.
- [ ] Access tokens are not persisted in localStorage or logs.
- [ ] The UI handles loading, empty, unauthorized, and server-error states.
- [ ] Lint, typecheck, tests, and production build pass.

## 8. Done and handoff

Complete the deliverable: **a browser-verified authenticated React/TypeScript shell**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-032: scaffolded React dashboard with secure sessions and typed API access`

Next: [Day 033](step-033.md) — Visualize latency errors tokens and cost.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
