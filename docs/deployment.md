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
- `OPENROUTER_API_KEY` is required for server-side OpenRouter evaluation
  (`run_llm.py evaluate` and admin-triggered re-evaluation).
- Local LLM evaluation is handled by the eval client (`docs/eval-client.md`),
  not by the server.

See `.env.example` for all available settings.

### Reloading .env in production

pydantic-settings reads `.env` at startup. To apply changes after editing the
file on the server:

```bash
# Edit the file (SSH into the server first)
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
in vps-infra for the live configuration). LLM evaluation is not scheduled —
it's opt-in via `scripts/eval_client.py` (pull-based) or manually invoking
`scripts/run_llm.py evaluate`.

In production (Podman), use `podman exec` instead of `docker compose exec`:

```bash
# /etc/cron.d/craft-dashboard

# Open-issue and release refresh — every 10 minutes (keeps Issues/Releases dashboards current)
*/10 * * * * root podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source github --mode open

# Full collection (open + closed issues, launchpad) — daily at 2 AM UTC
# Refreshes closed issues per the per-project schedule (refresh-interval-days).
0 2 * * * root podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source all --mode full

# Database backup — daily at 3 AM UTC
0 3 * * * root podman exec -i vps-infra_postgres_1 pg_dump -U craft_dashboard craft_dashboard | gzip > /opt/vps-infra/backups/craft-dashboard-$(date +\%Y\%m\%d).sql.gz
```

If you use local LLM evaluation, run `scripts/eval_client.py` from a trusted
machine that can reach the server over HTTPS. Server-side evaluation
(`scripts/run_llm.py`) has no cron entry in vps-infra; there is no daily
scheduled eval job — run it manually whenever you like.
