# Day 016 — Integrate LiteLLM and gated real-provider adapters

> Week 3 · Working LLM gateway · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Add the brief provider abstraction while keeping local and fake execution as the default.

- Wrap LiteLLM behind the existing Provider port.
- Add an optional local OpenAI-compatible model endpoint and config-gated OpenAI/Anthropic adapters.
- Normalize streaming capability, usage, errors, and provider request identifiers.

**Deliverable:** a tested LiteLLM adapter with optional local/real provider configuration

## 2. Why this matters

LiteLLM reduces vendor coupling, but the application still owns safety, budgets, normalized contracts, and testability.

## 3. Previous work done

**Immediate dependencies:** [Day 013](step-013.md), [Day 014](step-014.md), [Day 015](step-015.md).

Before starting, all 15 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 15 earlier steps</summary>

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

</details>

## 4. How to build it

- Pin a reviewed LiteLLM version in the lockfile and verify its current async completion/streaming API before implementation.
- Resolve credentials through the secret adapter; never accept provider keys from ordinary chat requests.
- Keep an allowlist of provider/model/base URL combinations to prevent SSRF and accidental egress.
- Mark real-provider tests opt-in with explicit credentials and spend caps; default CI uses fakes/local fixtures.
- Record adapter conformance tests so every provider satisfies the same contract or reports unsupported capabilities honestly.

## 5. Workflow for today

1. Add the LiteLLM adapter and dependency lock.
2. Run conformance tests with mocked SDK responses and the local fake endpoint.
3. Configure a local model only if hardware permits; otherwise document it as optional.
4. If explicitly approved, run one tiny real-provider smoke test per configured vendor.
5. Compare normalized usage/errors and document provider-specific differences.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
.venv/Scripts/python -m pytest backend/tests/unit/test_litellm_adapter.py backend/tests/contract/test_provider_conformance.py -q
.venv/Scripts/python -m pytest backend/tests -m "not real_provider" -q
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] Default tests make no paid or external provider calls.
- [ ] Adapter outputs satisfy the same normalized contract as fakes.
- [ ] Unsupported capabilities and unknown usage are explicit.
- [ ] Provider destinations and credentials are allowlisted and secret-safe.

## 8. Done and handoff

Complete the deliverable: **a tested LiteLLM adapter with optional local/real provider configuration**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-016: integrated LiteLLM behind the port with local and gated real adapters`

Next: [Day 017](step-017.md) — Record paid attempts and enforce idempotency.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
