# Deployment

craft-dashboard runs as a Docker container alongside a PostgreSQL container,
managed by Docker Compose. The Docker image is published to GHCR on every push
to `main`.

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
| Host     | `postgres` (Docker service name) |

For **production**, `.env` must include `DATABASE_URL` and `DB_PASSWORD` with
real credentials. See the [Production deployment](#production-deployment) section.

The other key settings in `.env` are API tokens:

```
GITHUB_TOKEN=<your GitHub fine-grained token>
ADMIN_TOKEN=<a random string for the admin API>
```

For LLM evaluation, also set:

```
# Option A: OpenRouter (recommended for production)
LLM_BACKEND=openrouter
OPENROUTER_API_KEY=<your key>

# Option B: Local LLM server
LLM_BACKEND=local
LOCAL_LLM_URL=https://192.168.1.10:8443/v1
LOCAL_LLM_API_KEY=<your bearer token>
```

See `.env.example` for all available settings.

## Local development with Docker

The `docker-compose.yml` file provides a full local stack:

```
docker compose up --build
```

This starts:
- **app**: the craft-dashboard FastAPI app on port 8000
- **postgres**: PostgreSQL 16 database

The app runs Alembic migrations on startup, so the database is ready
immediately. Visit `http://localhost:8000/` to see the dashboard.

To stop and remove all data:

```
docker compose down -v
```

## Production deployment

### Prerequisites

- A VPS or server with Docker Engine and Docker Compose installed
- A domain name pointing to the server IP (for HTTPS)
- A reverse proxy (nginx or caddy) for TLS termination

### Deploy

1. Create a `docker-compose.yml` on the server:

```yaml
services:
  app:
    image: ghcr.io/mr-cal/craft-dashboard:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy

  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: craft_dashboard
      POSTGRES_USER: craft_dashboard
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U craft_dashboard"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pgdata:
```

2. Create a `.env` file on the server. Start from `.env.example` and set all
   required values. The production `.env` must include:

```
# Database — must match POSTGRES_PASSWORD in docker-compose.yml
DB_PASSWORD=<a strong random password>
DATABASE_URL=postgresql+asyncpg://craft_dashboard:<DB_PASSWORD>@postgres/craft_dashboard

GITHUB_TOKEN=<your GitHub fine-grained token>
ADMIN_TOKEN=<a random string for the admin API>
```

   See `.env.example` for all available settings.

3. Pull and start:

```
docker compose pull
docker compose up -d
```

The app runs Alembic migrations automatically on startup.

### Update

```
docker compose pull
docker compose up -d
```

The GHCR image is rebuilt on every push to `main`, tagged as `latest` and
`sha-<commit>`.

## Migrating data

### Import from a SQL dump

Import the dump **before** starting the app. The app runs Alembic migrations on
startup; if you import into an already-migrated database, the restore will fail
with "relation already exists" errors and FK violations (the dump's table order
differs from Alembic's).

```bash
# 1. Start only the database
docker compose up -d postgres

# 2. Wait for it to be healthy, then import
gunzip -c craft-dashboard-initial.sql.gz | \
  docker compose exec -T postgres psql -U craft_dashboard craft_dashboard

# 3. Start the app (Alembic sees the existing schema and skips migrations)
docker compose up -d
```

If you already ran `docker compose up` and the database has been migrated, drop
and recreate the database before importing:

```bash
docker compose stop app

docker compose exec -T postgres psql -U craft_dashboard postgres \
  -c "DROP DATABASE craft_dashboard; CREATE DATABASE craft_dashboard;"

gunzip -c craft-dashboard-initial.sql.gz | \
  docker compose exec -T postgres psql -U craft_dashboard craft_dashboard

docker compose up -d
```

### Export a dump

```
docker compose exec -T postgres pg_dump -U craft_dashboard craft_dashboard | \
  gzip > craft-dashboard-$(date +%Y%m%d).sql.gz
```

## Scheduled tasks

Data collection and LLM evaluation run as cron jobs on the host, using
`docker compose exec` to run scripts inside the app container:

```bash
# /etc/cron.d/craft-dashboard

# Data collection — daily at 2 AM UTC
0 2 * * * root cd /opt/craft-dashboard && docker compose exec -T app python scripts/collect_data.py --source all

# LLM evaluation — daily at 6 AM UTC
0 6 * * * root cd /opt/craft-dashboard && docker compose exec -T app python scripts/run_llm.py evaluate --open-only

# Database backup — daily at 3 AM UTC
0 3 * * * root cd /opt/craft-dashboard && docker compose exec -T postgres pg_dump -U craft_dashboard craft_dashboard | gzip > backups/craft-dashboard-$(date +\%Y\%m\%d).sql.gz
```

The `-T` flag disables pseudo-TTY allocation (required for cron).
