# Day 003 — Add the local quality and security gate

> Week 1 · Foundation and architecture · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Make formatting, typing, tests, and basic security checks runnable before feature work begins.

- Create a local check script and GitHub Actions test workflow.
- Add Ruff, mypy, Pytest, Bandit, pip-audit, and detect-secrets checks.
- Run Semgrep through CI/container tooling and define a later Trivy image gate.

**Deliverable:** a working local/CI quality gate with documented scanner coverage

## 2. Why this matters

Small, frequent checks prevent fragile code and leaked credentials from accumulating until the release week.

## 3. Previous work done

**Immediate dependencies:** [Day 001](step-001.md), [Day 002](step-002.md).

Before starting, all 2 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 2 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions
- [ ] [Day 002](step-002.md) — initialized Git, minimal structure, .venv, locked dependencies, and smoke test

</details>

## 4. How to build it

- Put repeatable commands in scripts/check.sh; a Makefile may wrap it but must not be required on Windows.
- Install Python scanners only in .venv; Semgrep may not support native Windows cleanly, so use its official container or CI runner after verifying current usage.
- Configure detect-secrets with a reviewed baseline containing no real secrets; do not blanket-ignore findings.
- Use least-privilege workflow permissions and pin action revisions after reviewing current releases.
- Make findings actionable: fix true positives, document justified false positives, and never disable the whole scanner to obtain green CI.

## 5. Workflow for today

1. Run each tool locally and record its baseline result.
2. Create scripts/check.sh with fail-fast behavior.
3. Add a pull-request workflow using the same locked dependencies.
4. Introduce a harmless test violation to prove the gate fails, then remove it.
5. Document how to triage scanner findings and which checks wait until images exist.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
bash scripts/check.sh
.venv/Scripts/python -m bandit -r backend/app
.venv/Scripts/python -m pip_audit -r backend/requirements.lock
.venv/Scripts/python -m detect_secrets scan
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [ ] The local gate and CI run the same core checks.
- [ ] A deliberately invalid type or failing test causes a nonzero exit.
- [ ] Python scanners use .venv, not global packages.
- [ ] No real credential is present in the secret-scan baseline.

## 8. Done and handoff

Complete the deliverable: **a working local/CI quality gate with documented scanner coverage**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-003: added repeatable lint, type, test, dependency, and secret checks`

Next: [Day 004](step-004.md) — Start PostgreSQL and Redis with Docker Compose.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
