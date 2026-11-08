# Day 001 — Define the product scope and safety contract

> Week 1 · Foundation and architecture · Core roadmap  
> Plan for about 2 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Turn the full brief into an achievable first release with explicit safety and success boundaries.

- Write a one-page product scope covering the six modules and the seven failure stories in the brief.
- Create a lightweight STRIDE threat-model sketch and recovery A/B/C policy table.
- Write the first ADR choosing a modular monolith and define measurable portfolio targets without claiming they are achieved.

**Deliverable:** a reviewed scope, threat sketch, recovery policy table, and first ADR

## 2. Why this matters

A clear boundary prevents a monitoring demo from growing into fifteen microservices. Safety rules must be product requirements before code can accidentally bypass them.

## 3. Previous work done

No previous implementation work. The SentinelAI folder starts empty; today establishes the contract for everything that follows.

## 4. How to build it

- Create only needed documents under docs/architecture, docs/security, and docs/adr; do not generate an empty skeleton for every future module.
- Define MVP users: Owner, Admin, Operator, Viewer; define synthetic demo applications and the request lifecycle from the brief.
- Record category A actions allowed only within preapproved limits, B actions requiring human approval, and C actions never automatic.
- Resolve the stack conflict: Celery/Redis locally, SQS in AWS, Kafka/LangGraph/Kubernetes optional after release.
- List evidence required for release: tenant tests, idempotency tests, 200 quality cases, recovery tests, real benchmarks, and honest cloud status.

## 5. Workflow for today

1. Preserve the supplied brief under docs/product/project-brief.md, then read it and highlight mandatory MVP behavior versus advanced ideas.
2. Draw the six-module diagram and synchronous/asynchronous request paths.
3. Write the scope, non-goals, threat sketch, and ADR-0001.
4. Review every proposed recovery action against A/B/C and the prompt-injection boundary.
5. Create a requirement-to-evidence checklist and ask whether each item can be tested locally.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
ls
git status
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [x] The scope names all six modules and explains why the first architecture is a modular monolith.
- [x] Every recovery action is classified A, B, or C; model output has no direct execution authority.
- [x] The stack evolution avoids two active telemetry queues.
- [x] SLOs, coverage, and capacity numbers are labeled targets, not results.

## 8. Done and handoff

Complete the deliverable: **a reviewed scope, threat sketch, recovery policy table, and first ADR**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-001: defined MVP scope, six modules, safety categories, and architecture decisions`

Next: [Day 002](step-002.md) — Initialize the repository and Python environment.

## Safety note

Git may not be initialized yet; git status can legitimately fail today. Do not create remote repositories or cloud resources.
