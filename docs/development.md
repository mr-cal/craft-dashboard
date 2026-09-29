# Development

## Prerequisites

Install these tools on your development machine:

- Python 3.12 or newer
- [uv](https://astral.sh/uv)
- Podman with `podman compose`
- git

The repository contains `docker-compose.yml`, and local documentation uses Podman to run it. PostgreSQL runs in the Compose stack; pytest uses local test databases and fixtures.

## Setup

From the repository root:

```bash
make setup
cp .env.example .env
```

Fill in `.env` only for features that need external services. Local Compose supplies `DATABASE_URL` for the app container.

## Running locally

From the repository root:

```bash
make dev-seeded
```

This builds the image, starts PostgreSQL and the app, loads the same fixture
data the end-to-end suite uses, and prints the URL. Remove it with:

```bash
make dev-down
```

For hot reload against an already available database, run:

```bash
make dev
```

Apply migrations manually with:

```bash
make migrate
```

The container also runs `alembic upgrade head` during startup.

## Tests

From the repository root:

```bash
make test
make test-cov
make test-e2e
```

`make test` runs the pytest suite under `tests/`. End-to-end tests live under `tests/end_to_end/` and run only through `make test-e2e`.

Run a specific test file or test:

```bash
uv run pytest tests/unit/test_config.py -v
uv run pytest tests/unit/test_config.py::TestDashboardConfig::test_load_config_from_file -v
uv run pytest -k test_dashboard_shows_project -v
```

## Formatting, linting, and type checking

From the repository root:

```bash
make format
make lint
```

`make format` runs Ruff fixes and formatting. `make lint` runs Ruff checks, Ruff format diff, the documentation drift checker, and ty.

## Project layout

```text
craft_dashboard/          FastAPI app, models, routes, collectors, services, LLM code, templates, and static assets
scripts/                  Operational scripts and LLM worker commands
alembic/                  Alembic migrations
tests/unit/               Unit tests
tests/integration/        Integration tests
tests/end_to_end/         Browser-level tests
Dockerfile                OCI image build
docker-compose.yml        Local app and PostgreSQL stack
craft-dashboard.toml      Project, maintainer, forum, and filtering configuration
```

See [reference.md](reference.md) for command and environment variable details.
