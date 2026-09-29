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
| `EVAL_API_TOKEN` | empty | Bearer token for `/api/eval/*`. |
| `EVAL_TRANSCRIPT_RETENTION_DAYS` | `30` | Retention window for non-latest evaluation transcripts. |
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
| `DASHBOARD_URL` | Base URL for the evaluation HTTP API. |
| `LLM_BASE_URL` | OpenAI-compatible LLM endpoint. |
| `LLM_API_KEY` | LLM provider API key. |
| `LLM_MODEL` | Single model used for summary and scoring when split models are unset. |
| `LLM_MODEL_SUMMARY` | Summary model override. |
| `LLM_MODEL_SCORING` | Scoring model override. |
| `LLM_CA_CERT` | CA certificate for the LLM endpoint. |
| `DASHBOARD_CA_CERT` | CA certificate for the dashboard server. |
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

| Target | Command |
|---|---|
| `make setup` | Install dev, lint, and type dependencies. |
| `make format` | Ruff check with fixes, then Ruff format. |
| `make lint` | Ruff check, Ruff format diff, docs check, and ty. |
| `make test` | Run pytest. |
| `make test-cov` | Run pytest with coverage. |
| `make dev` | Run Uvicorn with reload. |
| `make migrate` | Run Alembic upgrade head. |
| `make collect` | Run `scripts/collect_data.py --source all`. |
| `make llm` | Run `scripts/run_llm.py evaluate --open-only`. |
| `make migrate-csv` | Run CSV migration script. |
| `make build` | Build the OCI image with Podman or Docker. |
| `make clean` | Remove build and test artifacts. |
| `make test-e2e` | Run end-to-end tests. |

### `craft-dashboard`

| Command | Options |
|---|---|
| `craft-dashboard serve` | `--host`, `--port`, `--reload` |
| `craft-dashboard collect` | `--source github|launchpad|all`, `--config-file`, `--limit`, `--verbose` |
| `craft-dashboard mirrors sync` | `--config-file` |
| `craft-dashboard commit-scanner run` | `--config-file`, `--dry-run`, `--top-k`, `--threshold` |

### Data collection

| Script | Options |
|---|---|
| `scripts/collect_data.py` | `--source github|launchpad|all`, `--limit`, repeatable `--project`, `--verbose`/`-v`, `--full-refresh`, `--force-schedule`, `--mode open|full|all|rotation` |
| `scripts/collect_forum_data.py` | `--mode backfill|refresh|all`, repeatable `--forum`, `--refresh-interval-days`, `--years-lookback`, `--max-requests-per-batch`, `--verbose`/`-v` |
| `scripts/backfill_snapshots.py` | No Click options. Uses `DATABASE_URL`. |
| `scripts/gc_transcripts.py` | No Click options. Uses settings environment. |
| `scripts/lp_bug_report.py` | No Click options. Uses `DATABASE_URL`. |

### LLM evaluation

| Command | Options |
|---|---|
| `scripts/run_llm.py evaluate` | `--interval`, `--limit`/`--max-evaluations`, `--project`, `--open-only`/`--all-issues`, `--force`, `--incomplete`, `--stale-days`, `--issue`, `--concurrency`, `--llm-backend openrouter|local|copilot-acp`, `--verbose`, `--log`, `--slow-eval`, `--min-delay`, `--max-delay`, `--tool-delay` |
| `scripts/run_llm.py clear-evaluations` | `--project`, `--yes`/`-y` |
| `scripts/llm/canary.py` | `--server`, `--token`, repeatable `--issue`, `--model-summary`, `--model-scoring`, `--openrouter-api-key`, `--llm-backend openrouter|local`, `--timeout-seconds` |

## Cron schedule

The repository does not contain production cron files. The deployment repository owns the schedule. Operational commands in this repository support these job shapes:

| Job | Command shape |
|---|---|
| Open GitHub refresh | `scripts/collect_data.py --source github --mode open` |
| Full GitHub and Launchpad refresh | `scripts/collect_data.py --source all --mode full` |
| Rotation refresh | `scripts/collect_data.py --mode rotation` |
| Forum collection | `scripts/collect_forum_data.py --mode all` |
| Transcript cleanup | `scripts/gc_transcripts.py` |
| Mirror sync | `craft-dashboard mirrors sync` |
| Commit scan | `craft-dashboard commit-scanner run` |
| Database backup | `pg_dump` against the PostgreSQL container |

## Database tables

Table names come from `craft_dashboard/models/`.

| Table | Model file | Purpose |
|---|---|---|
| `projects` | `project.py` | Tracked projects and display metadata. |
| `issues` | `issue.py` | GitHub issues, GitHub PRs, and Launchpad bugs. |
| `llm_evaluations` | `llm_evaluation.py` | Evaluation results, versions, costs, locks, hashes, and embeddings. |
| `evaluation_transcripts` | `evaluation_transcript.py` | Stored evaluation transcripts. |
| `snapshots` | `snapshot.py` | Daily project metrics for trends. |
| `releases` | `release.py` | Release and branch release data. |
| `dependencies` | `dependency.py` | Craft library dependency data. |
| `refresh_schedule` | `refresh_schedule.py` | Project/source refresh timing and failures. |
| `collection_watermarks` | `collection_watermark.py` | Successful collection watermarks. |
| `collection_runs` | `collection_run.py` | Collection run health records. |
| `issue_activities` | `issue_activity.py` | Detected issue and PR activity. |
| `forum_topics` | `forum.py` | Discourse topics. |
| `forum_backfill_state` | `forum.py` | Discourse backfill cursors and category cache. |
| `eval_queue_snapshots` | `eval_queue_snapshot.py` | Evaluation queue depth history. |
| `issue_links` | `issue_link.py` | Related-work links between issues. |
| `commit_scan_runs` | `commit_scan_run.py` | Commit scanner run summaries. |
| `commit_scan_evidence_paths` | `commit_scan_evidence_path.py` | Paths supporting evaluation evidence. |

## HTTP endpoints

Endpoint list comes from `craft_dashboard/routes/` and `craft_dashboard/app.py`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Dashboard overview. |
| `GET` | `/health` | App and database health check. |
| `GET` | `/issues` | Issue list page. |
| `GET` | `/issues/table` | Issue table partial. |
| `GET` | `/issues/{project}/{number}` | Issue detail page. |
| `GET` | `/issues/export` | Filtered issue export. |
| `GET` | `/stats` | Redirect to trends. |
| `GET` | `/stats/dependencies` | Dependency page. |
| `GET` | `/stats/dependencies/data` | Dependency JSON data. |
| `GET` | `/stats/releases` | Release page. |
| `GET` | `/stats/cadence` | Release cadence page. |
| `GET` | `/stats/trends` | Trends page. |
| `GET` | `/stats/trends/all-data` | Full trend JSON data. |
| `GET` | `/stats/trends/data` | Per-project trend JSON data. |
| `GET` | `/stats/trends/chart` | Trend chart partial. |
| `GET` | `/stats/triage` | Triage summary page. |
| `GET` | `/engagement/forums` | Forum activity page. |
| `GET` | `/engagement/forums/data` | Forum activity JSON data. |
| `POST` | `/admin/auth` | Create admin session. |
| `POST` | `/admin/logout` | Clear admin session. |
| `GET` | `/admin/status` | Collection and evaluation status JSON. |
| `GET` | `/admin` | Admin ingestion page. |
| `GET` | `/admin/evaluations` | Admin evaluations page. |
| `GET` | `/admin/schedule` | Admin schedule page. |
| `GET` | `/admin/recent-activity` | Recent activity partial. |
| `GET` | `/admin/recent-evaluations` | Recent evaluations partial. |
| `GET` | `/admin/collection-runs/{run_id}/issues` | Issues collected by a run. |
| `POST` | `/admin/refresh` | Queue data refresh. |
| `GET` | `/admin/health` | Detailed admin health JSON. |
| `GET` | `/admin/logs` | Recent service logs. |
| `GET` | `/api/eval/next` | Lease the next evaluation item. |
| `POST` | `/api/eval/result` | Store evaluation output. |
| `POST` | `/api/eval/release` | Release a claim. |
| `POST` | `/api/eval/related` | Related-issue search by request body. |
| `GET` | `/api/eval/related` | Related-issue search by query string. |
| `GET` | `/api/eval/issue` | Resolve an issue reference. |
| `POST` | `/api/eval/quota-pause` | Report worker quota pause. |
| `GET` | `/api/eval/status` | Evaluation queue counts. |
| `GET` | `/api/eval/projects` | Project org map for workers. |
| `GET` | `/api/eval/clear` | Count evaluations to clear. |
| `DELETE` | `/api/eval/clear` | Delete evaluations. |
| `POST` | `/api/eval/clear` | Delete evaluations. |

`/admin/*` mutation endpoints require admin authentication. `/api/eval/*` endpoints require `EVAL_API_TOKEN`.

## CI and image publishing

| Workflow | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | Push and pull request to `main` | Runs setup, lint, and tests. |
| `.github/workflows/e2e.yml` | Push, pull request, manual | Runs end-to-end tests. |
| `.github/workflows/publish.yml` | Push to `main` | Builds and pushes the GHCR image and dispatches `craft-dashboard-updated` to `mr-cal/vps-infra`. |
