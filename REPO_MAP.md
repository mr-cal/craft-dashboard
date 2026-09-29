# Repository map

Start here, then read the `AGENTS.md` in whichever package you are changing.
`agents.md` at the root holds the workflow rules.

## Request path

```
routes/ -> services/ -> repositories/ -> models/ -> PostgreSQL
```

Imports only ever point right. `import-linter` contracts in `pyproject.toml`
fail the build when they do not.

## Packages

| Path | Role |
|---|---|
| `craft_dashboard/routes/` | FastAPI handlers, one module per page |
| `craft_dashboard/services/` | Business logic and aggregation |
| `craft_dashboard/repositories/` | All SQL |
| `craft_dashboard/models/` | SQLAlchemy ORM models |
| `craft_dashboard/collectors/` | GitHub, Launchpad, and Discourse ingestion |
| `craft_dashboard/llm/` | Model clients, prompts, embeddings, evaluation |
| `craft_dashboard/commit_scanner/` | Maps new commits to issues worth re-evaluating |
| `craft_dashboard/git_mirrors/` | Bare mirror clone, fetch, and read-only access |
| `craft_dashboard/templates/` | Jinja2 templates |
| `craft_dashboard/static/` | CSS, JS, and vendored frontend libraries |

## Shared modules

| Path | Role |
|---|---|
| `craft_dashboard/settings.py` | Environment-backed settings |
| `craft_dashboard/config.py` | `craft-dashboard.toml` parsing |
| `craft_dashboard/http_retry.py` | The single retry policy for all outbound HTTP |
| `craft_dashboard/rate_limit.py` | The shared slowapi limiter |
| `craft_dashboard/db.py` | Async engine and session factory |

## Outside the package

| Path | Role |
|---|---|
| `scripts/` | Operator entry points, cron jobs, and backfills |
| `alembic/versions/` | Migrations; the app runs `alembic upgrade head` at startup |
| `tests/unit/` | Fast, no database |
| `tests/integration/` | Real database, still local |
| `tests/end_to_end/` | Playwright against a container |
| `docs/` | development, operations, reference, architecture |

## Where things are decided

| Question | File |
|---|---|
| Which projects are tracked? | `craft-dashboard.toml` |
| What is a hotfix? | `craft-dashboard.toml`, `[hotfix-min-versions]` |
| How often does collection run? | `docs/reference.md` |
| What are the environment variables? | `docs/reference.md` |
| Why is it shaped this way? | `docs/architecture.md` |
