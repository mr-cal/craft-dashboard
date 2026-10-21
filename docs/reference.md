# Reference

## Environment variables

Application and worker settings are configured via environment variables or a `.env` file. Annotated settings, default values, and operational descriptions are documented in [`.env.example`](../.env.example).

Programmatic definitions and types live in:
- `craft_dashboard/settings.py` for application, database, semantic search, and collector settings.
- `scripts/llm/cli.py` for evaluation worker backend and HTTP connection settings.

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

## CLI commands

### Make targets

Run `make` or `make help` from the repository root to view all development and test targets.

### `craft-dashboard`

Run `<command> --help` for options.

| Command | Purpose |
|---|---|
| `craft-dashboard serve` | Start the web application. |
| `craft-dashboard collect` | Collect issue, PR, and release data. |
| `craft-dashboard mirrors sync` | Clone or update bare git mirrors. |
| `craft-dashboard commit-scanner run` | Scan commits to invalidate stale evaluations. |

### Data collection

Run `<script> --help` for options.

| Script | Purpose |
|---|---|
| `scripts/collect_data.py` | Collect GitHub and Launchpad issues and pull requests. |
| `scripts/collect_forum_data.py` | Collect Discourse forum posts and topics. |
| `scripts/backfill_snapshots.py` | Backfill daily metrics snapshots from issue history. |
| `scripts/gc.py` | Clean up superseded transcripts and historical evaluations. |
| `scripts/gc_transcripts.py` | Compatibility alias for `scripts/gc.py`. |
| `scripts/lp_bug_report.py` | Generate Launchpad bug reports. |

### LLM evaluation

Run `<command> --help` for options.

| Command | Purpose |
|---|---|
| `craft-dashboard eval run` / `scripts/run_llm.py evaluate` | Continuous HTTP evaluation worker. |
| `craft-dashboard eval status` / `scripts/run_llm.py status` | Check worker connectivity and queue status. |
| `craft-dashboard eval clear` / `scripts/run_llm.py clear-evaluations` | Delete stored evaluations by project or filter. |
| `scripts/manage_eval_tokens.py create` | Create an evaluation bearer token. |
| `scripts/manage_eval_tokens.py list` | List evaluation bearer tokens and stats. |
| `scripts/manage_eval_tokens.py revoke` | Revoke an evaluation bearer token. |

## Cron schedule

The repository does not contain production cron files. The deployment repository owns the schedule. Operational commands in this repository support these job shapes:

| Job | Command shape |
|---|---|
| Open GitHub refresh | `scripts/collect_data.py --source github --mode open` |
| Full GitHub and Launchpad refresh | `scripts/collect_data.py --source all --mode full` |
| Rotation refresh | `scripts/collect_data.py --mode rotation` |
| Forum collection | `scripts/collect_forum_data.py --mode all` |
| Retention garbage collection | `scripts/gc_transcripts.py` (runs `scripts/gc.py`) |
| Mirror sync | `craft-dashboard mirrors sync` |
| Commit scan | `craft-dashboard commit-scanner run` |
| Database backup | `pg_dump` against the PostgreSQL container |

## Database schema

SQLAlchemy ORM models defining the database schema live in `craft_dashboard/models/`. Migrations are managed with Alembic under `alembic/versions/`.

## HTTP endpoints

Interactive API documentation and schema specifications are served at [https://craft-dashboard.name/docs](https://craft-dashboard.name/docs) (or `/docs` on local instances).

`/admin/*` mutation endpoints require admin authentication. `/api/eval/*` endpoints require an evaluation bearer token.

## CI and image publishing

| Workflow | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | Push and pull request to `main` | Runs setup, lint, and tests. |
| `.github/workflows/e2e.yml` | Push, pull request, manual | Runs end-to-end tests. |
| `.github/workflows/publish.yml` | Push to `main` | Builds and pushes the GHCR image and dispatches `craft-dashboard-updated` to `mr-cal/vps-infra`. |
| `.github/workflows/release.yml` | Tag push (`*.*.*`) | Builds and publishes `craft-dashboard` to PyPI and creates a GitHub release. |
