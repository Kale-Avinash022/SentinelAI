# Day 002 — Initialize the repository and Python environment

> Week 1 · Foundation and architecture · Core roadmap  
> Plan for about 2–3 focused hours. Treat this as a pacing estimate, not a deadline.

[Back to the roadmap](README.md)

## 1. What to build

**Goal:** Create a reproducible Python-first repository without installing packages globally.

- Initialize Git and the minimal root/backend/docs/tests structure.
- Create a Python 3.12+ .venv, pyproject tool configuration, and pip-tools input/lock files.
- Add .gitignore, .env.example with placeholders, and a smoke test.

**Deliverable:** a clean repository with a locked virtual environment and passing smoke test

## 2. Why this matters

Reproducibility and secret-safe defaults are easier to establish before dependencies and application code spread across the project.

## 3. Previous work done

**Immediate dependencies:** [Day 001](step-001.md).

Before starting, all 1 earlier steps should be implemented and their acceptance checks passing. This is the expected cumulative state, not a claim that the work is already complete.

<details>
<summary>Previous work done — all 1 earlier steps</summary>

- [ ] [Day 001](step-001.md) — defined MVP scope, six modules, safety categories, and architecture decisions

</details>

## 4. How to build it

- Use backend/app and backend/tests as in the brief; add folders only when needed.
- Ignore .venv, .env, coverage output, local reports containing secrets, Terraform state, and node_modules. Commit example settings and lockfiles, never credentials.
- Create backend/requirements.in and backend/requirements-dev.in; the development input includes runtime requirements plus pytest, ruff, mypy, and pip-tools.
- Compile locked requirements with pip-tools inside .venv and install the development lock with the venv interpreter.
- Configure pytest pythonpath for backend and strict-enough ruff/mypy settings to catch real errors without burying a beginner in noise.

## 5. Workflow for today

1. Inspect the folder and initialize Git only after confirming there is no existing repository state to preserve.
2. Create the minimal files and a local .env from placeholders without real secrets.
3. Create .venv and confirm python resolves inside it.
4. Compile/install locked dependencies into .venv and run the smoke test.
5. Review git status and make the first local checkpoint only after secrets are excluded.

## 6. Commands and checks

Run from the SentinelAI repository root in Git Bash. Python commands use the project virtual environment; activate it once per shell with `source .venv/Scripts/activate` after Day 02. When today introduces Python packages, update the requirements inputs, recompile locks, and install with `.venv/Scripts/python -m pip install -r backend/requirements-dev.lock`; never use global pip.

```bash
git init
python --version
python -m venv .venv
.venv/Scripts/python -m pip install pip-tools
.venv/Scripts/python -m piptools compile backend/requirements.in -o backend/requirements.lock
.venv/Scripts/python -m piptools compile backend/requirements-dev.in -o backend/requirements-dev.lock
.venv/Scripts/python -m pip install -r backend/requirements-dev.lock
.venv/Scripts/python -m pytest backend/tests/unit -q
git status --short
```

Adapt paths only if your implemented repository intentionally differs. Verify current tool APIs before copying version-sensitive examples from external documentation.

## 7. Acceptance checklist

- [x] Python is 3.12+ and package installation targets .venv.
- [x] The smoke test passes from the repository root.
- [x] Lockfiles are reproducible and .env/.venv/state files are ignored.
- [x] .env.example contains placeholders only.

## 8. Done and handoff

Complete the deliverable: **a clean repository with a locked virtual environment and passing smoke test**. Save relevant evidence from the checks, update the roadmap checkbox, and review the diff before making your own checkpoint commit.

Suggested checkpoint message: `day-002: initialized Git, minimal structure, .venv, locked dependencies, and smoke test`

Next: [Day 003](step-003.md) — Add the local quality and security gate.

## Safety note

Use synthetic data and deterministic local providers. Do not run paid-provider tests, cloud apply/destroy, public publishing, or high-risk recovery without explicit approval.
