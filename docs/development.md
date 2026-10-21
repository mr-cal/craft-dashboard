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

Run a specific test file, class, method, or keyword:

```bash
uv run pytest tests/unit/test_config.py -v
uv run pytest tests/unit/<test_file>.py::<TestClass>::<test_method> -v
uv run pytest -k "<filter-expression>" -v
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

## Configuration file

`craft-dashboard.toml` is loaded by `craft_dashboard/config.py`.

| Key | Purpose |
|---|---|
| `craft-applications` | Application repositories. |
| `craft-libraries` | Shared library repositories. |
| `craft-other` | Other tracked repositories. |
| `craft-projects` | GitHub repositories collected by the GitHub collector. |
| `craft-consumers` | Projects that need additional repository context during evaluation. |
| `refresh-interval-days` | Full-refresh interval used by schedules. |
| `hide-prs` | Projects whose PRs are hidden from the dashboard. |
| `hide-releases` | Projects hidden from release views. |
| `launchpad-projects` | Launchpad projects collected as separate dashboard projects. |
| `maintainers` | GitHub maintainers used for author classification. |
| `launchpad-maintainers` | Launchpad maintainers used for author classification. |
| `bots` | Bot authors used for author classification. |
| `[issues.filter]` | Per-project issue numbers excluded from dashboard queries. |
| `[hotfix-min-versions]` | Minimum hotfix branch versions for release tracking. |
| `[initial-release-dates]` | Inception timestamps for release cadence. |
| `[initial-release-tags]` | Initial tags for release cadence. |
| `[forums.<name>]` | Discourse forum configuration. |

## Environment variables

Application and worker settings are configured via environment variables or a `.env` file. Annotated settings, default values, and operational descriptions are documented in [`.env.example`](../.env.example).

Programmatic definitions and types live in:
- `craft_dashboard/settings.py` for application, database, semantic search, and collector settings.
- `scripts/llm/cli.py` for evaluation worker backend and HTTP connection settings.

## Database schema and migrations

SQLAlchemy ORM models defining the database schema live in `craft_dashboard/models/`. Migrations are managed with Alembic under `alembic/versions/`.

## CI workflows

| Workflow | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | Push and pull request to `main` | Runs setup, lint, and tests. |
| `.github/workflows/e2e.yml` | Push, pull request, manual | Runs end-to-end tests. |
| `.github/workflows/publish.yml` | Push to `main` | Builds and pushes the GHCR image and dispatches `craft-dashboard-updated` to `mr-cal/vps-infra`. |
| `.github/workflows/release.yml` | Tag push (`*.*.*`) | Builds and publishes `craft-dashboard` to PyPI and creates a GitHub release. |

## HTTP API documentation

Interactive API documentation and OpenAPI specifications are served at `/docs` (or `https://craft-dashboard.name/docs` on production).

`/admin/*` mutation endpoints require admin authentication. `/api/eval/*` endpoints require an evaluation bearer token.
