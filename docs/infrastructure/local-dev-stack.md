# Local Development Stack — PostgreSQL & Redis (Docker Compose)

> **Introduced:** Day 004 · **Scope:** reproducible local dependencies with safe
> persistence and separate, least-privilege database identities.
> Observability, AWS-local, and Kafka services run under separate Compose
> profiles and never start by default.

## 1. Quick start

Run from the repository root (Docker Desktop must be running):

```bash
docker compose config                          # validate the Compose specification
docker compose --profile core up -d            # start PostgreSQL 16 + Redis 7 only
docker compose --profile core ps               # both services must report "healthy"
docker compose --profile core logs --tail=100  # inspect role-bootstrap output
```

Connection details live in the ignored `.env`; placeholders are in `.env.example`.
Both services bind **only to `127.0.0.1`** — nothing is exposed to the LAN or
internet. PostgreSQL is published on host port **5433** (container port 5432)
because a native Windows PostgreSQL installation already owns host port 5432
on the development machines; Redis uses host port 6379.

## 2. Persistence model

| Action | Command | Data outcome |
|---|---|---|
| Restart containers | `docker compose restart` | **Preserved** — named volumes survive |
| Stop stack | `docker compose --profile core stop` | **Preserved** — volumes untouched |
| Remove containers | `docker compose --profile core down` | **Preserved** — volumes outlive containers |
| Delete volumes | `docker compose --profile core down -v` | **DESTROYED** — irreversible |

PostgreSQL and Redis data are stored in the named Docker volumes
`sentinel_postgres_data` and `sentinel_redis_data`. Host directories are never
bind-mounted for database files, because the repository lives under OneDrive
and sync conflicts would corrupt live database storage.

**Deleting volumes is a separate destructive decision.** Never run
`down -v` (or `docker volume rm`) unless the data loss is explicitly intended
and approved.

## 3. Database identities

`infra/docker/postgres/init-roles.sh` runs automatically on the **first**
initialization of an empty `postgres_data` volume and provisions:

| Role | Purpose | Privileges |
|---|---|---|
| `postgres` | Local Compose bootstrap superuser | Container initialization only; never used by the application |
| `sentinel_migrator` | Schema owner / Alembic DDL | `LOGIN`, `CREATEDB`, `NOSUPERUSER`, `NOCREATEROLE`, `NOREPLICATION`, `NOBYPASSRLS`; owns `public` schema |
| `sentinel_app` | Application runtime DML | `LOGIN`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOREPLICATION`, `NOBYPASSRLS`; `USAGE` on schema, DML default privileges only; **not** an owner |

`sentinel_app` lacking ownership, `SUPERUSER`, and `BYPASSRLS` is what makes
later row-level security enforcement (Day 010) meaningful.

These credentials are **local-development only** (placeholders mirrored in
`.env.example`). They must never be reused in AWS or any shared environment.

## 4. Verifying roles and privileges

```bash
# Connect as each role (passwords come from local .env)
docker compose --profile core exec -T postgres psql -U sentinel_migrator -d sentinel_ai -c "SELECT current_user, current_setting('is_superuser')"
docker compose --profile core exec -T postgres psql -U sentinel_app   -d sentinel_ai -c "SELECT current_user, current_setting('is_superuser')"

# Confirm least-privilege flags (expect rolcanlogin=t, rolsuper=f, rolbypassrls=f)
docker compose --profile core exec -T postgres psql -U sentinel_migrator -d sentinel_ai -c \
  "SELECT rolname, rolcanlogin, rolsuper, rolcancreate, rolbypassrls FROM pg_roles WHERE rolname IN ('sentinel_migrator','sentinel_app')"

# Confirm sentinel_app cannot create objects (should fail with permission denied)
docker compose --profile core exec -T postgres psql -U sentinel_app -d sentinel_ai -c "CREATE TABLE should_fail (id int)"
```

## 5. Verifying survival across a restart

```bash
docker compose --profile core exec -T postgres psql -U sentinel_migrator -d sentinel_ai -c \
  "CREATE TABLE IF NOT EXISTS day004_persistence_probe (note text); INSERT INTO day004_persistence_probe VALUES ('survived-restart')"
docker compose restart
docker compose --profile core ps   # wait until healthy again
docker compose --profile core exec -T postgres psql -U sentinel_migrator -d sentinel_ai -c \
  "SELECT * FROM day004_persistence_probe"   # row must still exist
```

Clean up the probe afterwards: `DROP TABLE day004_persistence_probe;`

## 6. Health checks and resource limits

- `postgres`: `pg_isready` every 5s (5 retries, 10s start period).
- `redis`: `redis-cli ping` every 5s (5 retries, 5s start period).
- CPU/memory limits and reservations are declared under `deploy.resources`
  so the local stack cannot silently consume the whole workstation.
