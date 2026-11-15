# Week 1 Foundation Checkpoint — Clean-Setup Verification Report (Day 007)

> **Date:** 2026-11-15 · **Scope:** Days 001–007 (Phase 1: Foundation & Architecture)
> **Objective:** prove that another machine can reproduce the SentinelAI foundation
> from committed artifacts alone — no global pip, no local-file copying, no
> undocumented manual steps.

---

## 1. Exercise design

- Source of truth: a **fresh `git clone` of repository HEAD `ab05ca0`** into a
  temporary directory **outside the working tree** (`%TEMP%\sentinel-clean-clone`).
- A **new `.venv`** was created there (Python 3.14.3 via `py -3.14`); the original
  development environment was not touched.
- The operator followed **only the committed README quickstart**, locked
  dependency files, and committed configuration templates.
- The clone was removed after evidence was captured.

## 2. Step-by-step results

| README step | Command | Result |
|---|---|---|
| §1 Clone + venv | `py -3.14 -m venv .venv` | ✅ Python 3.14.3 |
| §2 Locked installs | `python -m pip install -r backend\requirements.lock -r backend\requirements-dev.lock` | ✅ exit 0, no global pip, no resolver errors |
| §3 Configuration | `Copy-Item .env.example .env` | ✅ placeholders match the compose stack; **zero real secrets copied** |
| §4 Compose | `docker compose config --quiet` | ✅ valid spec (Postgres 5433→5432, Redis 6379, localhost-only) |
| §5 Migrations | `alembic -c backend\alembic.ini upgrade head` / `current` | ✅ idempotent re-run; `0001_baseline (head)` |
| §6 Quality gates | `scripts\check.ps1` | ✅ **7/7 gates pass** (Ruff format 35 files, Ruff lint, Mypy 27 sources, 19/19 unit, Bandit, pip-audit 0 CVEs, detect-secrets vs committed baseline) |
| §6 Full tests | `pytest backend\tests -q` | ✅ **34/34** (unit + contract + integration vs live stack) |
| §7 API + smoke | `uvicorn app.main:app` + curl | ✅ `/health` 200 alive · `/ready` 200 with live DB+Redis probe latencies · `/api/v1/version` · `/api/v1/echo` deterministic · 404 in the standard error envelope with `X-Request-ID` |

## 3. Foundation gaps found and closed during this checkpoint

1. **No root README existed** (and `pyproject.toml` referenced one) — authored
   `README.md` covering the implemented foundation only; future phases live in a
   clearly labeled Roadmap section, not the feature list.
2. **Runtime logs were tracked** (`uvicorn.log`, `uvicorn.out`) — untracked with
   `git rm --cached` (local copies kept) and added to `.gitignore`; repository
   output pollution is now structurally impossible.
3. **Stale detect-secrets baseline** — the Day 006 test files' hashed
   placeholders were only in the uncommitted baseline; a clean clone would have
   failed gate 7. Baseline committed in `360e91e`.
4. **Ambiguous interpreter** — the machine's default `python` is 3.11 (< project
   requirement 3.12+). The README now pins the working invocation (`py -3.14`)
   so a fresh machine cannot silently build a broken venv.

## 4. Documented deviations and carried items

- **Compose stack reuse:** the exercise used the already-running named volumes
  (`sentinel_postgres_data`, `sentinel_redis_data`) instead of recreating them
  from scratch, because re-testing first-boot role bootstrap requires a
  destructive `down -v` of development data. First-boot reproducibility of
  `init-roles.sh` remains evidenced from Day 004 and is **carried as an accepted
  assumption**, not re-proven today.
- **Clone source:** cloned from the local repository path at committed HEAD;
  the Day 006/007 checkpoint commits had not yet been pushed to `origin/main`
  at exercise time, so origin content lagged HEAD by those commits. Push is a
  user action.

## 5. Week 1 acceptance review

All acceptance checks for Days 001–006 were re-verified in this session or in
their recorded per-step evidence logs (internal step tracker): clean environment and
locks (002), 7-gate parity local↔CI (003), healthy least-privilege stack with
volume persistence (004), idempotent async migration pipeline with dual DSN
identities (005), and the live-verified FastAPI shell contracts — liveness/
readiness split, single error envelope, pre-parse size bound, metadata-only
logs (006). No unresolved Week 1 issues remain open.

## 6. Reproducing this exercise

```powershell
git clone <repo-url> $env:TEMP\sentinel-clean-clone
cd $env:TEMP\sentinel-clean-clone
py -3.14 -m venv .venv; .venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.lock -r backend\requirements-dev.lock
Copy-Item .env.example .env
docker compose --profile core up -d
$env:PYTHONPATH = "backend"
python -m alembic -c backend\alembic.ini upgrade head
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
python -m pytest backend\tests -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Verdict: Phase 1 (Week 1) foundation is reproducible from committed
artifacts. Foundation accepted; Phase 2 (Days 008–014, auth & tenant
isolation) is unblocked.**
