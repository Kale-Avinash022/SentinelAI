# SentinelAI

Enterprise AI Operations (AIOps) and Reliability Platform — a modular-monolith
LLM gateway with tenant isolation, telemetry, risk detection and safe recovery.

**Current status: Week 1 foundation.** This README documents only what is
implemented and verifiable today. Later-phase capabilities are listed under
[Roadmap](#roadmap-not-yet-implemented) and are *not* part of the quickstart.

---

## What works today

| Area | Implemented |
|---|---|
| API shell | FastAPI app factory (`create_app`), versioned `/api/v1` router (`GET /version`, `POST /echo`) |
| Health semantics | `GET /health` liveness (never touches dependencies) vs `GET /ready` readiness (bounded Postgres + Redis probes, 503 envelope on outage) |
| Edge contracts | `X-Request-ID` correlation (echo/generate/sanitize), single error envelope `{code, message, request_id, retryable}`, pre-parse request body-size limit (413) |
| Logging | Structured JSON, metadata-only (request id, method, route template, status, duration, client IP) — never bodies or credentials |
| Configuration | Typed Pydantic settings validated at startup from `.env` / environment; migration-role DSN rejected for the app runtime |
| Data layer | Async SQLAlchemy 2.x with dual least-privilege DSNs, Alembic async environment, intentional baseline migration |
| Local stack | Docker Compose `core` profile: PostgreSQL 16 + Redis 7, localhost-only bindings, named volumes, health checks, first-boot role bootstrap (`sentinel_migrator` DDL / `sentinel_app` DML) |
| Quality gates | 7-check local gate + CI: Ruff format/lint, Mypy, Pytest (unit), Bandit, pip-audit, detect-secrets ([docs](docs/security/quality-and-security-gates.md)) |

## Prerequisites

- **Git**
- **Docker Desktop** (Compose v2)
- **Python 3.12 – 3.14** (3.14 tested). On Windows the default `python` alias
  may be 3.11 — use the launcher explicitly, e.g. `py -3.14`.

All commands below are PowerShell (repository root); Git Bash equivalents are
noted where they differ. Never use a global pip — everything installs into `.venv`.

## Quickstart

### 1. Clone and create a virtual environment

```powershell
git clone <your-fork-url> SentinelAI
cd SentinelAI
py -3.14 -m venv .venv                 # bash: python3.12 -m venv .venv
.venv\Scripts\Activate.ps1             # bash: source .venv/Scripts/activate
```

### 2. Install locked dependencies

```powershell
python -m pip install -r backend\requirements.lock -r backend\requirements-dev.lock
```

Locks are compiled with `pip-tools` from the `*.in` inputs; rebuild with
`python -m piptools compile backend\requirements.in` after changing inputs.

### 3. Create local configuration

```powershell
Copy-Item .env.example .env
```

`.env.example` contains **local-development placeholders only** (they match the
compose stack's defaults and carry zero real secrets). `.env` itself is
gitignored — never commit real credentials.

### 4. Start PostgreSQL and Redis

```powershell
docker compose --profile core up -d
docker compose --profile core ps      # both services should report "healthy"
```

Notes:

- PostgreSQL is published as `127.0.0.1:5433 -> 5432` because a native Windows
  PostgreSQL installation previously occupied host port 5432; Redis is on
  `127.0.0.1:6379`. Both are reachable from the host only.
- First boot runs `infra/docker/postgres/init-roles.sh`, creating the
  `sentinel_migrator` (schema owner / DDL) and `sentinel_app` (DML-only,
  non-owner, `NOBYPASSRLS`) roles.
- `docker compose stop` **preserves** the named volumes; deleting them is a
  separate, destructive `down -v`. Never bind-mount OneDrive-synced folders
  for database storage.

### 5. Apply database migrations

```powershell
$env:PYTHONPATH = "backend"
python -m alembic -c backend\alembic.ini upgrade head
python -m alembic -c backend\alembic.ini current   # -> 0001_baseline (head)
```

Migrations run exclusively under the `sentinel_migrator` identity via
`MIGRATION_DATABASE_URL` (resolved by the Alembic env from `.env`); the
application runtime never holds DDL rights.

### 6. Run the quality gate and tests

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check.ps1   # bash: bash scripts/check.sh
python -m pytest backend\tests -q                            # full suite (needs the stack up)
```

`check.ps1`/`check.sh` runs the same seven gates as CI. The integration and
contract suites use isolated databases and ASGI transport respectively — they
never touch the development data beyond the read-only probes.

### 7. Start the API and smoke-test it

```powershell
$env:PYTHONPATH = "backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

From another shell:

```powershell
curl.exe http://127.0.0.1:8000/health          # -> {"status":"alive", ...}
curl.exe http://127.0.0.1:8000/ready           # -> {"status":"ready", "checks":[database, redis]}
curl.exe http://127.0.0.1:8000/api/v1/version  # -> {"api_version":"v1", ...}
curl.exe -X POST http://127.0.0.1:8000/api/v1/echo -H "Content-Type: application/json" -d '{\"message\":\"hello\",\"repeat\":2}'
```

Interactive API docs (generated from the Pydantic contracts): <http://127.0.0.1:8000/docs>.
Every response echoes `X-Request-ID`; all errors use the single envelope shape.

## Repository layout

```
backend/app/               # modular monolith package
  main.py                  #   app factory + lifespan (ASGI target app.main:app)
  core/                    #   settings, logging, middleware, error envelope
  api/                     #   health endpoints, /api/v1 router
  infrastructure/          #   async engine/session lifecycle
  models/                  #   declarative Base + naming-convention metadata
backend/migrations/        # Alembic environment and revisions
backend/tests/             # unit / contract / integration suites
docs/                      # architecture, security and infrastructure docs
infra/docker/postgres/     # first-boot role bootstrap script
scripts/check.{ps1,sh}     # local 7-gate quality & security runner
```

## Roadmap (not yet implemented)

Deliberately excluded from the feature list above — these land in later phases:
authentication & RBAC (Week 2), tenant row-level security (Week 2), the LLM
gateway with idempotency and streaming (Week 3), telemetry/outbox/workers
(Week 4), metrics dashboard & React UI (Week 5), risk/incidents (Week 6),
quality evaluation (Week 7), recovery orchestration (Week 8), hardening,
benchmarks, and cloud deployment (Weeks 9–12).

## Security notes

- Synthetic data and deterministic local behavior only; no paid-provider or
  cloud calls exist yet.
- The app connects as the least-privilege `sentinel_app` role; DDL is confined
  to Alembic under `sentinel_migrator`.
- Logs and error responses are metadata-only: no request bodies, prompt
  content, stack traces, DSNs or credentials ever leave the process.
- See the [STRIDE threat model](docs/security/stride-threat-model.md) and
  [quality & security gates](docs/security/quality-and-security-gates.md).
