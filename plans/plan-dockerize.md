# Plan: Dockerize craft-dashboard

Containerize the craft-dashboard application so it can run as a Docker service
on the VPS infrastructure (see `plan-vps-infra.md`).

## Current State

- Python 3.12 FastAPI app served by Gunicorn + Uvicorn workers
- PostgreSQL 16 database (currently host-installed, moving to Docker container)
- Ansible provisioning deploys directly to a VPS (clones repo, creates venv, runs
  via systemd)
- No Dockerfile or docker-compose.yml exists
- Scheduled tasks: data collection cron, LLM evaluation cron, DB backups

## What Changes

### 1. Dockerfile (Multi-Stage Build)

```dockerfile
# ---- Build stage ----
FROM python:3.12-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY craft_dashboard/ craft_dashboard/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/
COPY craft-dashboard.toml ./

# Install the app and its dependencies into a virtual environment
RUN uv venv /app/.venv \
    && uv pip install --python /app/.venv/bin/python -e . psycopg2-binary

# ---- Runtime stage ----
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /build /app
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Run Alembic migrations then start Gunicorn
CMD ["sh", "-c", "alembic upgrade head && gunicorn --bind 0.0.0.0:8000 --workers 4 --worker-class uvicorn.workers.UvicornWorker 'craft_dashboard.app:create_app()'"]
```

**Design notes:**
- Multi-stage keeps the final image small (no build tools, uv, etc.)
- `alembic upgrade head` runs on startup so migrations are applied automatically
  on deploy
- `psycopg2-binary` for PostgreSQL (no need to compile from source)
- `libpq5` is the only runtime dependency needed for psycopg2-binary
- The entrypoint runs migrations then starts the app — simple and reliable

### 2. .dockerignore

```
.git
.github
.venv
.pytest_cache
.ruff_cache
__pycache__
*.pyc
.env
.env.example
.envrc
tests/
docs/
provisioning/
plans/
*.egg-info
logs.txt
```

### 3. GHCR Publish Workflow

`.github/workflows/publish.yml` — triggered on push to `main`:

```yaml
name: Publish Docker Image

on:
  push:
    branches: [main]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # needed for setuptools_scm versioning

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=raw,value=latest
            type=sha,prefix=

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

**Tags:** Every push to main produces `latest` and `sha-<commit>` tags.

### 4. Local Development with Docker (Optional)

A `docker-compose.dev.yml` for running locally with a PostgreSQL container:

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: craft_dashboard
      POSTGRES_USER: craft_dashboard
      POSTGRES_PASSWORD: devpassword
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U craft_dashboard"]
      interval: 5s
      timeout: 3s
      retries: 5
```

Run with: `docker compose -f docker-compose.dev.yml up --build`

### 5. What to Keep / Remove

**Keep (unchanged):**
- `Makefile` — local dev commands (format, lint, test) still work without Docker
- `provisioning/` — keep for reference/rollback, but mark as deprecated in README
- All application code, tests, scripts
- CI workflow (`ci.yml`) — tests still run in GitHub Actions without Docker

**No changes to application code** — the app already reads config from environment
variables via pydantic-settings. No code changes needed for Docker.

### 6. Database Migration Path

When moving from the current VPS to the Dockerized setup:

1. Dump from current VPS: `pg_dump craft_dashboard > dump.sql`
2. Start the new Docker PostgreSQL container
3. Import: `docker compose exec -T postgres psql -U craft_dashboard craft_dashboard < dump.sql`
4. Verify: hit `/health`, check dashboard data

### 7. Scheduled Tasks in Docker

The cron jobs (data collection, LLM evaluation) run via `docker compose exec`:

```bash
# From the host crontab
docker compose exec -T craft-dashboard python scripts/collect_data.py --source all
docker compose exec -T craft-dashboard python scripts/run_llm.py evaluate --open-only
```

The `-T` flag disables pseudo-TTY allocation (required for cron).

## Files to Create

| File | Description |
|------|-------------|
| `Dockerfile` | Multi-stage build for the craft-dashboard app |
| `.dockerignore` | Exclude unnecessary files from the Docker build context |
| `.github/workflows/publish.yml` | Build and push Docker image to GHCR on push to main |
| `docker-compose.dev.yml` | Optional local development stack |

## Implementation Order

1. Create `Dockerfile` and `.dockerignore`
2. Build and test locally: `docker build -t craft-dashboard . && docker run --rm -p 8000:8000 --env-file .env craft-dashboard`
3. Create `.github/workflows/publish.yml`
4. Push to main, verify image appears in GHCR
5. Create `docker-compose.dev.yml` for convenient local dev
