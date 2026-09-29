# Architecture

## Shape of the system

craft-dashboard separates collection, evaluation, and presentation.

```text
External APIs ── collectors ── PostgreSQL ── FastAPI/Jinja2 ── browser
                                  ▲
                                  │
                         evaluation worker
```

The web app serves database-backed pages and JSON endpoints. External API calls happen in scripts and workers, not during dashboard page rendering. This keeps request latency tied to database queries and template rendering.

## Collection pipeline

`scripts/collect_data.py` loads `craft-dashboard.toml`, creates or updates project rows, and collects GitHub issues, GitHub pull requests, releases, dependencies, and Launchpad bugs. GitHub collection uses open, full, all, and rotation modes. Watermarks and refresh schedules keep repeated runs incremental where the selected mode supports it.

`scripts/collect_forum_data.py` collects Discourse topics for configured forums. It stores topic rows and backfill state for the Engagement forum activity page. The endpoint and pagination rationale lives in the module docstring for `craft_dashboard/collectors/forum.py`.

Collectors record health in `collection_runs`, successful positions in `collection_watermarks`, scheduled refresh state in `refresh_schedule`, and user-visible changes in `issue_activities`.

## Evaluation pipeline

Evaluation is pull-based. A worker calls `GET /api/eval/next`, evaluates the returned issue or PR, then submits the result to `POST /api/eval/result`. The worker can run in the production container or on a developer machine with access to the dashboard URL.

```text
worker ── GET /api/eval/next ──> dashboard
worker <──── issue payload ───── dashboard
worker ─ POST /api/eval/result > dashboard
```

The server authenticates evaluation endpoints with `EVAL_API_TOKEN`. Claims use database locks with expiry so multiple workers can poll without intentionally handling the same item. Result submission validates the content hash, stores the evaluation, computes embeddings server-side, updates related-work links, and clears commit-scan evidence paths for the issue.

## Evaluation versioning

`craft_dashboard/llm/evaluator.py` defines the active evaluation version for each issue state and item type. The API compares latest stored rows with the active version and content hash when it builds the pending queue. A version change makes matching rows eligible for evaluation through normal polling; operators do not need a database update to enqueue them.

Each issue has one latest evaluation row. Older rows remain available for audit and rollback unless explicitly deleted. Transcripts for superseded rows are cleaned by `scripts/gc_transcripts.py` according to `EVAL_TRANSCRIPT_RETENTION_DAYS`.

## Git mirrors and commit scanning

Bare mirrors live under `CRAFT_DASHBOARD_MIRROR_DIR`. `craft-dashboard mirrors sync` clones missing mirrors and fetches existing mirrors. Evaluation tools use these mirrors for repository context. The commit scanner uses mirrors and embeddings to identify issues whose evidence may be invalidated by recent commits, then records scan summaries and evidence paths in the database.

## Web app

`craft_dashboard/app.py` creates the FastAPI app, configures templates, mounts static assets, installs routes, initializes the database session factory, and exposes `/health`.

Route modules have focused responsibilities:

- `dashboard.py`: overview page.
- `issues.py`: issue list, issue detail, table partial, and export.
- `stats.py`: trends, releases, dependencies, cadence, and triage views.
- `engagement.py`: forum activity views and JSON data.
- `admin.py`: admin pages, refresh trigger, health, logs, and fragments.
- `eval_api.py`: evaluation worker API.

Templates use Jinja2. HTMX powers partial updates. Chart.js renders trend and engagement charts.

## Database and migrations

SQLAlchemy ORM models live in `craft_dashboard/models/`. Alembic migrations live in `alembic/`. The container command runs `alembic upgrade head` before Gunicorn starts. Local development can apply the same migrations with `make migrate`.

See [reference.md](reference.md#database-tables) for table names.

## Configuration

There are two configuration layers:

- `craft-dashboard.toml` defines tracked projects, maintainers, bots, filters, hotfix thresholds, release seed data, and forums.
- Environment variables configure secrets, database access, evaluation API auth, embedding services, database pool sizing, semantic search, mirrors, and commit scanning.

See [reference.md](reference.md#environment-variables) for the authoritative environment variable list.

## Container and deployment flow

The Dockerfile builds a Python 3.12 OCI image. Local development runs that image with `podman compose` and PostgreSQL. The publish workflow builds and pushes `ghcr.io/mr-cal/craft-dashboard:latest` on pushes to `main`, then sends a repository dispatch event to `mr-cal/vps-infra`. The deployment repository owns VPS service definitions, secrets, cron, backups, and container restarts.
