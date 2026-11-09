# SentinelAI — Quality & Security Gate Specification

> **Document:** `docs/security/quality-and-security-gates.md`  
> **Status:** Active Baseline  
> **Version:** 1.0.0 (Day 003)

---

## 1. Quality & Security Philosophy

SentinelAI enforces continuous, automated quality and security checks on every code commit and pull request. Rather than deferring audits to pre-release phases, we fail fast locally and in CI to guarantee that:
1. No unformatted or untyped code enters the codebase.
2. Unit tests pass with zero regressions.
3. No known CVE vulnerabilities exist in our runtime dependency graph.
4. No hardcoded credentials or API tokens leak into Git history.
5. Insecure Python constructs (e.g., `eval`, `shell=True`, insecure deserialization) are blocked before merging.

---

## 2. Gate Tooling & Coverage Matrix

| Category | Tool | Execution Scope | Enforcement Level | Notes |
|---|---|---|---|---|
| **Code Formatting** | `ruff format` | Repository Root | Blocking | Enforces uniform PEP 8 style with 100-character line length. |
| **Linting & Best Practices** | `ruff check` | Repository Root | Blocking | Rules: E, W, F, I (isort), B (bugbear), C4, UP (pyupgrade). |
| **Static Type Checking** | `mypy` | `backend/` | Blocking | Strict mode: `disallow_untyped_defs = true`, `warn_return_any = true`. |
| **Unit Test Suite** | `pytest` | `backend/tests/unit/` | Blocking | Fast execution (< 1s); zero warnings allowed. |
| **SAST (Python Security)** | `bandit` | `backend/app/` | Blocking | Scans AST for security vulnerabilities, insecure randoms, and injection risks. |
| **SCA (Dependency Vulnerabilities)** | `pip-audit` | `backend/requirements.lock` | Blocking | Queries PyPA Advisory DB & OSV for known CVEs. |
| **Secret Detection** | `detect-secrets` | Repository Root | Blocking | Validates against audited `.secrets.baseline`; fails on new unreviewed secrets. |
| **Advanced SAST** | `semgrep` | CI / Container | Blocking in CI | Runs rulesets: `p/security-audit`, `p/secrets`, `p/python`. Deferred from native Windows due to CLI compatibility. |
| **Container Vulnerability Gate** | `trivy` | Container Images | Blocking in Week 11 / CI | Scans base images (`python:3.12-slim`, `postgres:16-alpine`) once Docker packaging starts. |

---

## 3. Local Execution Guide

Developers must run the gate before pushing commits:

### On Windows PowerShell:
```powershell
.\scripts\check.ps1
```

### On Git Bash / Linux / macOS:
```bash
bash scripts/check.sh
```

---

## 4. Triage Protocol for Scanner Findings

1. **True Positives (Real Vulnerabilities / Secret Leaks):**
   * **Fix Immediately:** Remediate the code, rotate leaked keys immediately, and purge from Git history if committed.
   * **Never Suppress:** Never add `# nosec` or disable linter rules without documented security review.
2. **False Positives / Safe Placeholders (e.g. `.env.example` dummy credentials):**
   * Review finding carefully to ensure it contains **no real key or secret pattern**.
   * Update `.secrets.baseline` using `.venv/Scripts/python -m detect_secrets scan --baseline .secrets.baseline`.
   * Record justification in the pull request description.
3. **Dependency CVEs (Flagged by `pip-audit`):**
   * Update `backend/requirements.in` to a patched minimum version.
   * Recompile locks: `.venv/Scripts/python -m piptools compile backend/requirements.in -o backend/requirements.lock`.
   * Re-verify clean status with `pip-audit`.

