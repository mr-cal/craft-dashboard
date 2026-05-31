# Development

## Prerequisites

You need these on your local machine:

- Python 3.12+
- [uv](https://astral.sh/uv) (Python package manager)
- Docker Engine and Docker Compose
- git

PostgreSQL runs in a Docker container — you do not need to install it locally.
Unit and integration tests use SQLite in-memory databases and do not need
Docker.

## Setup

```
make setup
```

This runs `uv sync` and installs all dev, lint, and type-checking dependencies.

## Running the dev server

```
docker compose up --build
```

This starts the app on `http://localhost:8000` with PostgreSQL via Docker
Compose. The database credentials are hardcoded in `docker-compose.yml`
(user: `craft_dashboard`, password: `devpassword`).

For non-Docker development (e.g. `make dev` with hot reload), you still need
a `.env` file — but `DATABASE_URL` is set by Docker Compose and does not need
to be in `.env`. Copy `.env.example` to `.env` and fill in your API tokens.

## Tests

```
make test          # all tests (unit + integration, excludes e2e)
make test-cov      # same, with coverage report
make test-e2e      # end-to-end tests (requires Docker, see below)
```

Run a specific test file or test:

```
uv run pytest tests/unit/test_config.py -v
uv run pytest -k "test_dashboard_shows_project" -v
```

### Test layout

Tests are in `tests/` and split into three categories:

- `tests/unit/` -- fast, no database, no HTTP. Tests individual functions and
  classes in isolation.
- `tests/integration/` -- uses an in-memory SQLite database and FastAPI's
  `TestClient`. Tests routes, templates, and queries against real (but
  ephemeral) data.
- `tests/end_to_end/` -- requires Docker (builds and runs the app via Docker
  Compose). Uses `requests` and Puppeteer for browser-level checks. Marked with
  `@pytest.mark.e2e` and `@pytest.mark.slow`.

Integration tests patch SQLAlchemy's SQLite type compiler to handle JSONB
columns as TEXT. This happens in `tests/integration/conftest.py`.

### pytest markers

- `e2e` -- end-to-end tests (skipped unless `CRAFT_DASHBOARD_E2E=1`)
- `slow` -- tests that take a long time

## Linting and formatting

```
make format        # auto-fix with ruff
make lint          # check with ruff + ty (type checker)
```

The project uses ruff for both formatting and linting. Line length is 88
characters. Type checking is done with ty (not mypy).

After making changes, run in this order:

1. `make format`
2. `make lint`
3. `make test`

## Project layout

```
craft_dashboard/          # main Python package
  app.py                  # FastAPI app factory
  auth.py                 # admin token verification
  cli.py                  # click CLI entry point
  config.py               # TOML config loader (craft-dashboard.toml)
  database.py             # SQLAlchemy engine/session setup
  dependencies.py         # FastAPI dependency injection (DB session)
  enums.py                # IssueState, IssueType, IssueSource
  settings.py             # pydantic-settings (env vars / .env)
  utils.py                # datetime normalization
  models/                 # SQLAlchemy ORM models
  routes/                 # FastAPI route handlers
  collectors/             # data collection (GitHub, Launchpad, deps, snapshots)
  llm/                    # LLM evaluation (OpenRouter, local)
  templates/              # Jinja2 HTML templates
  static/                 # CSS, JS, favicon

scripts/                  # standalone scripts (run via docker compose exec)
tests/                    # pytest test suite
alembic/                  # database migration files
```

### Docker files

```
Dockerfile                # multi-stage build for the app container
.dockerignore             # files excluded from Docker build context
docker-compose.yml        # Docker Compose stack (app + postgres)
```
