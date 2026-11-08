# Day 020 — Add streaming cancellation and tenant rate limits

> Week 3 · Working LLM gateway · Core roadmap  
> Plan for about 3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Support streamed responses without losing usage accounting or retrying partial output unsafely.

- Add SSE/chat streaming through the Provider port.
- Propagate client cancellation and finalize partial/unknown usage.
- Implement atomic organization/application/key rate limits in Redis.

**Deliverable:** safe streaming and atomic tenant rate limiting

## 2. Why this matters

Streaming changes failure semantics: once bytes reach the client, transparently restarting on another provider can duplicate output and charges.

## 3. Previous work done

**Immediate dependencies:** [Day 013](step-013.md), [Day 015](step-015.md), [Day 016](step-016.md), [Day 017](step-017.md), [Day 018](step-018.md), [Day 019](step-019.md).

Before starting, all 19 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 19 earlier steps</summary>

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

</details>

## 4. How to build it

- Normalize stream start/delta/usage/end/error events and document the SSE contract.
- Do not retry or fallback after the first content byte; finalize the attempt as partial/failed/unknown with available usage.
- Use monotonic time for latency and separate time-to-first-token from total duration.
- Cancel upstream work when the client disconnects where the adapter supports it; record cancellation without assuming billing stopped.
- Use a reviewed Redis Lua script for rate-limit counter/window updates and return Retry-After consistently.

## 5. Workflow for today

1. Extend provider conformance fixtures for streaming.
2. Implement SSE route and cancellation cleanup.
3. Add finalization in a finally/exit path that survives disconnects.
4. Add atomic tenant rate limits and concurrency tests.
5. Verify metadata-only logs contain no streamed content.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/contract/test_streaming.py backend/tests/integration/test_rate_limits.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Streaming emits a documented event sequence and final usage state.
- [ ] Disconnects cancel upstream work where supported and finalize metadata.
- [ ] No automatic retry/fallback occurs after first content byte.
- [ ] Concurrent requests cannot exceed the configured tenant/key window.

## 8. Done and handoff

Complete the deliverable: **safe streaming and atomic tenant rate limiting**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-020: added SSE streaming, cancellation accounting, and tenant rate limits`

Next: [Day 021](step-021.md) — Verify the complete fake-provider gateway.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
