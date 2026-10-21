# Repository map

Quick navigation guide. See [docs/architecture.md](docs/architecture.md) for architectural design and layering rules.
Read the `AGENTS.md` in whichever package you are changing for contracts and invariants.
`agents.md` at the root holds the workflow rules.

## Request path

Data flows strictly rightwards:
```
routes/ -> services/ -> repositories/ -> models/ -> PostgreSQL
```

Imports only ever point right. `import-linter` contracts in `pyproject.toml`
enforce this.

## Packages and contracts

| Path | Role | Contract file |
|---|---|---|
| `craft_dashboard/routes/` | FastAPI handlers, one module per page | `craft_dashboard/routes/AGENTS.md` |
| `craft_dashboard/services/` | Business logic and aggregation | `craft_dashboard/services/AGENTS.md` |
| `craft_dashboard/repositories/` | All SQL | `craft_dashboard/repositories/AGENTS.md` |
| `craft_dashboard/models/` | SQLAlchemy ORM models | `docs/architecture.md` |
| `craft_dashboard/collectors/` | GitHub, Launchpad, and Discourse ingestion | `craft_dashboard/collectors/AGENTS.md` |
| `craft_dashboard/llm/` | Model clients, prompts, embeddings, evaluation | `craft_dashboard/llm/AGENTS.md` |
| `scripts/llm/` | Operator entry points for evaluation worker | `scripts/llm/AGENTS.md` |
| `scripts/collect/` | Operator entry points for data collection | `scripts/collect/AGENTS.md` |

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
| `docs/` | development, operations, architecture |

## Where things are decided

| Question | File |
|---|---|
| Which projects are tracked? | `craft-dashboard.toml` |
| What is a hotfix? | `craft-dashboard.toml`, `[hotfix-min-versions]` |
| How often does collection run? | `docs/operations.md` |
| What are the environment variables? | `docs/development.md` |
| Why is it shaped this way? | `docs/architecture.md` |
