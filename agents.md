# agents.md

## Before completing any task

Always run:

```bash
make format   # ruff format + ruff check --fix
make lint     # ruff check + ty check
make test     # pytest (unit + integration, ~495 tests)
```

Make sure all commands pass before marking a task complete.

Any changes to the Dockerfile or Alembic migrations should also verify that
`make build` succeeds.

## Before completing UI/UX tasks

Run the e2e tests when making UI or UX changes:

```bash
make test-e2e  # requires Docker, ~5-10 min
```

## Test layout

- `tests/unit/` — no database, no HTTP. Fast.
- `tests/integration/` — uses SQLite in-memory + FastAPI TestClient.
- `tests/end_to_end/` — builds the Docker image and runs Puppeteer browser
  tests. Marked `@pytest.mark.e2e`, skipped unless `CRAFT_DASHBOARD_E2E=1`.

Integration tests patch SQLAlchemy to treat JSONB columns as TEXT on SQLite.

## Container engines

- **Local development**: Docker / Docker Compose (`docker-compose.yml`)
- **Production**: Podman, managed by the vps-infra repo

In production, use `podman exec -i vps-infra_craft-dashboard_1` where the
docs say `docker compose exec -T app`, and `podman exec -i vps-infra_postgres_1`
where they say `docker compose exec -T postgres`.

## Key config files

- `craft-dashboard.toml` — project list, maintainers, bots, hotfix thresholds.
  Edit this to add or remove tracked repos.
- `.env` / `.env.example` — runtime secrets and feature flags. Not committed.
- `alembic/versions/` — database migrations. Always generate with
  `uv run alembic revision --autogenerate -m "<description>"`.

## Database

Schema is managed by Alembic. The app runs `alembic upgrade head` on every
startup, so migrations apply automatically on deploy.

When writing migrations, be careful with the vector extension — it's only
available in the `pgvector/pgvector:pg16` image, not `postgres:16-alpine`.

## Image publishing

Pushing to `main` triggers `.github/workflows/publish.yml`, which builds and
pushes `ghcr.io/mr-cal/craft-dashboard:latest` to GHCR, then dispatches a
`repository_dispatch` event to [mr-cal/vps-infra](https://github.com/mr-cal/vps-infra)
to trigger a redeploy. The vps-infra deploy workflow pulls the new image and restarts
the container.

A `VPSINFRA_PAT` secret must be set on this repo (Settings → Secrets and variables →
Actions) with a fine-grained PAT scoped to `mr-cal/vps-infra` with
**Actions: Read and write**.
