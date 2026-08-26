# Deployment

craft-dashboard runs as a container alongside a PostgreSQL container.
Locally it uses Podman Compose. In production it runs under Podman,
managed by the [vps-infra](https://github.com/mr-cal/vps-infra) repo.

## CI deployment

Pushing to `main` triggers `.github/workflows/publish.yml`, which builds and
pushes `ghcr.io/mr-cal/craft-dashboard:latest` to GHCR, then dispatches a
`craft-dashboard-updated` event to vps-infra. The vps-infra deploy workflow
pulls the new image and restarts the container automatically.

To enable the dispatch, add a `VPSINFRA_PAT` secret to this repo
(Settings → Secrets and variables → Actions) with a fine-grained PAT
scoped to `mr-cal/vps-infra` with **Contents: Read and write**.

## Configuration

### .env

App settings are configured via environment variables, read from `.env` by
pydantic-settings at startup. Copy the example file and fill in your values:

```
cp .env.example .env
```

You do **not** need to set `DATABASE_URL` in `.env` for local development — it
is hardcoded in `docker-compose.yml` and overrides anything in `.env`. The
local database credentials are:

| Setting  | Value |
|----------|-------|
| User     | `craft_dashboard` |
| Password | `devpassword` |
| Database | `craft_dashboard` |
| Host     | `postgres` (Podman Compose service name) |

For **production**, `.env` must include `DATABASE_URL` with real credentials.
The DB password is stored as a Podman secret (not in `.env`). See the
vps-infra README for production setup.

The other key settings in `.env` are API tokens:

```
GITHUB_TOKEN=<your GitHub fine-grained token>
ADMIN_TOKEN=<a random string for the admin API>
EVAL_API_TOKEN=<a random string for /api/eval/*>
```

Evaluation settings:

```
OPENROUTER_API_KEY=<your key>
```

- `EVAL_API_TOKEN` is required for the pull-based eval API (`/api/eval/*`).
- `OPENROUTER_API_KEY` is required for the continuous server-side
  `run_llm.py evaluate` service.
- Continuous and local-backend HTTP evaluation is documented in `docs/evaluate.md`.

See `.env.example` for all available settings.

### Bare git mirror storage

Deep evaluation's git tools use a shared bare-mirror directory on the VPS:

| VPS path | Mounted into | Mode |
|----------|--------------|------|
| `/opt/vps-infra/mirrors/<project>.git` | `craft-dashboard` | `:rw` |
| `/opt/vps-infra/mirrors/<project>.git` | `llm-evaluate` | `:ro` |

The required `mr-cal/vps-infra` compose change has already landed in commit
`68a080f`: it mounts the shared bind-mount into both containers and creates
`/opt/vps-infra/mirrors` on the VPS.

Peak RSS measurement (2026-08-26): materialized all 18 project mirrors (~205MB total) via `craft-dashboard mirrors sync` and ran a broad `git grep -e 'def '` across each mirror's HEAD, capturing peak RSS via `/usr/bin/time -v`. Observed peak: snapcraft.git at ~22.7MB (23228 KB), the largest repo in the set; all others ranged ~5.5-17MB. Two concurrent worst-case greps (~45MB) fit comfortably within the VPS's ~307MB available RAM alongside postgres/caddy/app, so `git_concurrency` stays at its default of 2 (no change needed to `settings.py`).

Because bare mirrors keep reflogs and Phase 2 sets `gc.auto=0`, git gc must be
run on an explicit schedule during a quiet window. That is future operational
work, not part of this phase's scope.

### Reloading .env in production

pydantic-settings reads `.env` at startup. To apply changes after editing the
file on the server:

```bash
# SSH into the server, then edit the file
ssh -tt root@167.99.14.211
nano /opt/vps-infra/.env

# Restart the app container to pick up the new values
podman restart vps-infra_craft-dashboard_1
```

The container restarts in a few seconds; Alembic migrations run on startup
but skip if the schema is already current.

## Local development with Podman

The `docker-compose.yml` file provides a full local stack and works with
`podman compose`:

```
podman compose up --build
```

This starts:
- **app**: the craft-dashboard FastAPI app on port 8000
- **postgres**: PostgreSQL 16 database

The app runs Alembic migrations on startup, so the database is ready
immediately. Visit `http://localhost:8000/` to see the dashboard.

To stop and remove all data:

```
podman compose down -v
```

## Production deployment

Production is managed by the [vps-infra](https://github.com/mr-cal/vps-infra)
repo. Pushing to `main` here triggers a GitHub Actions workflow that builds and
publishes the OCI image to GHCR; vps-infra's deploy workflow then pulls and
restarts the container automatically.

See the vps-infra README for first-time server setup, secrets, and
manual operations.

## Migrating data

### Import from a SQL dump (local)

Import the dump **before** starting the app. The app runs Alembic migrations on
startup; if you import into an already-migrated database, the restore will fail
with "relation already exists" errors.

```bash
# 1. Start only the database
podman compose up -d postgres

# 2. Wait for it to be healthy, then import
gunzip -c craft-dashboard-initial.sql.gz | \
  podman compose exec -T postgres psql -U craft_dashboard craft_dashboard

# 3. Start the app (Alembic sees the existing schema and skips migrations)
podman compose up -d
```

If you already ran `podman compose up` and the database has been migrated,
drop and recreate the database before importing:

```bash
podman compose stop app

podman compose exec postgres dropdb -U craft_dashboard craft_dashboard
podman compose exec postgres createdb -U craft_dashboard craft_dashboard

gunzip -c craft-dashboard-initial.sql.gz | \
  podman compose exec -T postgres psql -U craft_dashboard craft_dashboard

podman compose up -d
```

### Import from a SQL dump (production Podman)

```bash
# 1. Stop the app container
podman stop vps-infra_craft-dashboard_1

# 2. Drop and recreate the database
podman exec -i vps-infra_postgres_1 dropdb -U craft_dashboard craft_dashboard
podman exec -i vps-infra_postgres_1 createdb -U craft_dashboard craft_dashboard

# 3. Import (stream the file through SSH if running remotely)
gunzip -c craft-dashboard-YYYYMMDD.sql.gz | \
  podman exec -i vps-infra_postgres_1 psql -U craft_dashboard craft_dashboard

# 4. Recreate the app container (podman start fails on this server due to
#    a Netavark bug; podman-compose recreates it cleanly)
podman rm vps-infra_craft-dashboard_1
cd /opt/vps-infra
podman-compose -f docker-compose.craft-dashboard.yml up -d
```

### Export a dump (local)

```
podman compose exec -T postgres pg_dump -U craft_dashboard craft_dashboard | \
  gzip > craft-dashboard-$(date +%Y%m%d).sql.gz
```

### Export a dump (production Podman)

```
podman exec -i vps-infra_postgres_1 pg_dump -U craft_dashboard craft_dashboard | \
  gzip > craft-dashboard-$(date +%Y%m%d).sql.gz
```

## Scheduled tasks

Data collection runs as cron jobs on the host (see
[`cron.d/collect-data`](https://github.com/mr-cal/vps-infra/blob/main/cron.d/collect-data)
in vps-infra for the live configuration). LLM evaluation now runs via the
continuous `scripts/run_llm.py evaluate` HTTP-polling service.

In production (Podman), use `podman exec` instead of `docker compose exec`:

```bash
# /etc/cron.d/craft-dashboard

# Open-issue and release refresh — every 10 minutes (keeps Issues/Releases dashboards current)
*/10 * * * * root podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source github --mode open

# Full collection (open + closed issues, launchpad) — daily at 2 AM UTC
# Refreshes closed issues per the per-project schedule (refresh-interval-days).
0 2 * * * root podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source all --mode full

# Forum activity backfill+refresh (snapcraft/charmhub/rockcraft Discourse) — every 15 minutes
# Advances the historical backfill by one month per forum, and refreshes
# recent months every 5 days. See "collect_forum_data.py" in how-to.md.
*/15 * * * * root podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_forum_data.py --mode all

# Superseded evaluation transcript GC — daily at 4 AM UTC
# Deletes only old transcripts for non-latest evaluations; latest
# evaluation transcripts are retained indefinitely. Retention window is
# controlled by EVAL_TRANSCRIPT_RETENTION_DAYS in .env.
0 4 * * * root podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/gc_transcripts.py

# Database backup — daily at 3 AM UTC
0 3 * * * root podman exec -i vps-infra_postgres_1 pg_dump -U craft_dashboard craft_dashboard | gzip > /opt/vps-infra/backups/craft-dashboard-$(date +\%Y\%m\%d).sql.gz
```

If you use a local LLM backend, run `scripts/run_llm.py evaluate --llm-backend local`
from a trusted machine that can reach the server over HTTPS. Server-side
evaluation now runs as the continuous `scripts/run_llm.py evaluate` polling
service rather than a manual one-shot batch job.
