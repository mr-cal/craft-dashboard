# Reference

## Environment variables

Application settings come from `craft_dashboard/settings.py` and `.env.example`.

| Variable | Default | Purpose |
|---|---:|---|
| `DATABASE_URL` | `postgresql+asyncpg://localhost/craft_dashboard` | SQLAlchemy async database URL. |
| `GITHUB_TOKEN` | empty | GitHub API token for collectors. |
| `EMBEDDING_API_KEY` | empty | OpenRouter API key for semantic search and evaluation embeddings. |
| `ADMIN_TOKEN` | empty | Admin UI and admin API bearer token. |
| `DEBUG` | `false` | Enables FastAPI debug mode. |
| `HOST` | loopback interface | App host setting used by settings consumers. |
| `PORT` | `8000` | App port setting used by settings consumers. |
| `CONFIG_FILE` | `craft-dashboard.toml` | TOML config path. |
| `LOG_LEVEL` | `INFO` | Python logging level. |
| `EVAL_API_TOKEN` | empty | Shared evaluation bearer token fallback for `/api/eval/*`. |
| `EVAL_MIN_WORKER_VERSION` | `0.0.0` | Minimum worker SemVer required by `/api/eval/*`. |
| `EVAL_TRANSCRIPT_RETENTION_DAYS` | `30` | Retention window for non-latest evaluation transcripts. |
| `EVAL_RETENTION_DAYS` | `90` | Retention window for superseded evaluations. Evaluations marked `latest` are never deleted. |
| `EVAL_DAILY_SPEND_CAP_USD` | `0.0` | Daily evaluation spend cap; zero disables the cap. |
| `RELATED_ISSUES_TOP_N` | `10` | Related-issue result limit on issue detail pages. |
| `RELATED_ISSUES_SIMILARITY_THRESHOLD` | `0.70` | Related-issue embedding similarity floor. |
| `SEMANTIC_SEARCH_EMBEDDING_MODEL` | `openai/text-embedding-3-small` | Embedding model for issue search and summaries. |
| `SEMANTIC_SEARCH_TOP_N` | `10` | Semantic issue-search result limit. |
| `SEMANTIC_SEARCH_SIMILARITY_THRESHOLD` | `0.25` | Semantic issue-search similarity floor. |
| `DB_POOL_SIZE` | `5` | SQLAlchemy pool size. |
| `DB_MAX_OVERFLOW` | `10` | SQLAlchemy pool overflow. |
| `REFRESH_AGE_DAYS` | `7` | GitHub issue refresh age window. |
| `CRAFT_DASHBOARD_MIRROR_DIR` | `~/.cache/craft-dashboard/mirrors` | Bare git mirror directory. |
| `CRAFT_DASHBOARD_GIT_CONCURRENCY` | `2` | Concurrent git subprocess limit. |
| `COMMIT_SCANNER_TOP_K` | `10` | Semantic candidates per scanned commit. |
| `COMMIT_SCANNER_SIMILARITY_THRESHOLD` | `0.70` | Commit scanner semantic similarity floor. |
| `COMMIT_SCANNER_DAILY_INVALIDATION_WARN_THRESHOLD` | `200` | Admin warning threshold for daily invalidations. |

The LLM worker also reads these variables in `scripts/llm/cli.py`:

| Variable | Purpose |
|---|---|
| `CRAFT_DASHBOARD_URL` / `DASHBOARD_URL` | Base URL for the evaluation HTTP API. |
| `CRAFT_EVAL_TOKEN` / `EVAL_API_TOKEN` | Bearer token for the evaluation HTTP API. |
| `CRAFT_DASHBOARD_CA_CERT` / `DASHBOARD_CA_CERT` | CA certificate for the dashboard server. |
| `LLM_BASE_URL` | OpenAI-compatible LLM endpoint. |
| `LLM_API_KEY` | LLM provider API token. |
| `LLM_MODEL` | Single model used for summary and scoring when split models are unset. |
| `LLM_MODEL_SUMMARY` | Summary model override. |
| `LLM_MODEL_SCORING` | Scoring model override. |
| `LLM_CA_CERT` | CA certificate for the LLM endpoint. |
| `COPILOT_ACP_MODEL` | Model name for the `copilot-acp` backend. |
| `LLM_CONFIG_ERROR_DELAY_SECONDS` | Delay before worker exit on configuration errors. |

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
| `scripts/llm/canary.py` | Canary evaluation for specific issues. |
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
