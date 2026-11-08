# Day 015 — Define LLM contracts and deterministic fake providers

> Week 3 · Working LLM gateway · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Implement the brief chat contract against repeatable test providers before any paid integration.

- Define POST /api/v1/llm/chat request/response schemas and standard provider errors.
- Create a Provider port and deterministic fake adapters for success, latency, 429, 500, malformed, empty, and reset scenarios.
- Normalize usage, model/provider identity, status, latency, and trace correlation.

**Deliverable:** a fake-backed chat endpoint with stable normalized contracts

## 2. Why this matters

A stable internal contract lets the gateway, tests, and later LiteLLM adapters evolve without coupling business logic to vendor response shapes.

## 3. Previous work done

**Immediate dependencies:** [Day 001](step-001.md), [Day 006](step-006.md), [Day 014](step-014.md).

Before starting, all 14 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 14 earlier steps</summary>

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

</details>

## 4. How to build it

- Follow the brief model sentinel-auto, messages, metadata, options contract; bound message count, body size, max_tokens, and timeout.
- Use an explicit normalized result type with exact/estimated/unknown usage rather than vendor-specific dictionaries throughout the code.
- Seed fake behavior by fixture/config, not random global state; expose controls only in local/test settings.
- Map INVALID_REQUEST, UNAUTHORIZED, FORBIDDEN, RATE_LIMITED, PROVIDER_TIMEOUT, PROVIDER_UNAVAILABLE, INTERNAL_ERROR consistently.
- Keep prompt/response content only in the immediate call path; persistence introduced later is metadata-only.

## 5. Workflow for today

1. Write Pydantic schemas and contract examples from the brief.
2. Define the async provider interface and fake implementation.
3. Add the chat route calling a service, not provider SDK code directly.
4. Test each fake scenario and OpenAPI schema stability.
5. Document the normalized usage/error semantics for later adapters.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/contract/test_llm.py backend/tests/unit/test_fake_provider.py -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] The chat contract matches the brief under /api/v1.
- [ ] Every fake scenario is deterministic and selectable without network access.
- [ ] Usage is labeled exact, estimated, or unknown.
- [ ] Provider errors map to the standard envelope without leaking internals.

## 8. Done and handoff

Complete the deliverable: **a fake-backed chat endpoint with stable normalized contracts**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-015: defined chat contracts and deterministic multi-failure fake providers`

Next: [Day 016](step-016.md) — Integrate LiteLLM and gated real-provider adapters.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
