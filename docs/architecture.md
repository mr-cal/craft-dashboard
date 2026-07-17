# Architecture

## Overview

craft-dashboard is a read-only web application backed by a PostgreSQL database.
The web app never calls external APIs during request handling. GitHub and
Launchpad data is fetched by offline scripts that run on systemd timers. LLM
results can be produced either by optional server-side jobs or by a pull-based
local eval client that fetches work from the server and submits results back.

```
                          ┌──────────────────────────────────┐
                          │  cron / docker compose exec      │
                          │                                  │
  GitHub API  <───────────│  collect_data.py  (2 AM daily)   │───────> PostgreSQL
  Launchpad   <───────────│                                  │
                          │  run_llm.py       (6 AM daily)   │───────> PostgreSQL
  OpenRouter              <│                                  │
                          └──────────────────────────────────┘

                          ┌──────────────────────────────────┐
  browser  ──────────────>│  gunicorn + uvicorn workers      │
                          │    └── FastAPI (craft_dashboard)  │──────> PostgreSQL
                          └──────────────────────────────────┘
```

The separation means the web app stays fast regardless of how long data
collection takes, and a GitHub API outage doesn't break the dashboard.

## Data flow

1. `collect_data.py` fetches issues, PRs, releases, and dependencies from
   GitHub (and optionally Launchpad). It upserts them into the database and
   generates a daily snapshot (aggregate counts + median ages) for each project.

   Open issues/PRs and releases are fetched via GraphQL
   (`craft_dashboard/collectors/github_graphql.py`) rather than the REST API,
   which lets a single paginated query return nested fields (labels, review
   status, CI checks) that would otherwise require many REST round-trips.
   Closed-issue history still uses the REST API (`PyGithub`), since GraphQL
   pagination isn't a net win for the full-history backfill path. The
   collector tracks REST ("core") and GraphQL budgets separately
   (`GitHubCollector.check_rate_limit`) since they're independent quotas.

   Open-issue and release polling runs every 10 minutes in production (see
   `cron.d/collect-data` in `mr-cal/vps-infra`), using the `since` timestamp
   recorded in `collection_watermarks` to fetch only issues/PRs updated since
   the last successful run. A `collection_runs` row (scoped by `source`) acts
   as a concurrency guard so overlapping cron invocations for the same source
   don't run at once. Each detected issue/PR change is recorded as an
   `issue_activities` row, driving the admin page's "Recent Activity" feed —
   but note that the 10-minute pass only queries GraphQL's `states: [OPEN]`
   issues/PRs, so it can record "created"/"updated" activity quickly but
   cannot itself detect closures: an issue/PR that closes simply stops
   appearing in that pass's results. Closures are only recorded during the
   daily full-refresh pass (`--mode full`), which still uses the REST API for
   closed-issue history. In practice this means "closed" activity can lag up
   to a day behind the actual closure.

2. LLM evaluation can run in two ways:

   - `run_llm.py evaluate` performs optional server-side evaluation against
     OpenRouter and writes the evaluation (summary, suggested action, scores)
     back to PostgreSQL.
   - `scripts/eval_client.py` performs pull-based local evaluation. It requests
     the next issue from `/api/eval/next`, evaluates it against an
     OpenAI-compatible LLM, and submits the result to `/api/eval/result`.

   Both flows use content hashing to skip unchanged issues.

3. The FastAPI app reads everything from the database and renders HTML pages
   with Jinja2 templates. The issues page uses HTMX for filtering and
   pagination without full page reloads. The trends page uses Chart.js to
   render time series from a JSON API endpoint.

## Database schema

The main tables:

- `projects` -- one row per tracked repository. Has a `category` field
  (`application`, `library`, `aggregate`, `launchpad`, `other`). The
  `all-projects` row (category `aggregate`) is a synthetic project used for
  cross-project trend aggregation.

- `issues` -- every issue and PR from GitHub and Launchpad. Keyed by
  `(project_id, source, external_id)`. Stores title, body, author, state,
  labels, timestamps, and a JSONB `comments` field.

- `llm_evaluations` -- LLM triage results. Each issue can have multiple
  evaluations over time; the `latest` boolean flag (with a partial unique
  index) marks the current one. Contains a summary, suggested action, and
  a JSONB `scores` dict (including a `staleness` score).

- `snapshots` -- daily aggregate counts per project. Open/closed issue and PR
  counts split by author group (maintainer, external contributor, bot), plus
  median ages. Used by the trends charts.

- `releases` -- latest release version per project and branch. Includes commit
  count since the tag.

- `dependencies` -- which craft libraries each application depends on, with
  installed vs. latest version.

- `refresh_schedule` -- tracks when each project was last refreshed and when
  the next refresh is due. Used by the collector to avoid re-fetching projects
  that were recently updated.

- `collection_watermarks` -- records the last successful collection timestamp
  per `(project, source)` pair. Distinct from `refresh_schedule`: the watermark
  is set only on success, while the schedule drives when the next run fires.

- `collection_runs` -- one row per collection run. Stores start/finish times,
  status, issue counts, duration, and any per-project errors. Used by the admin
  status endpoint to surface collection health, and as a concurrency guard
  (scoped by `source`) to prevent overlapping runs for the same source.

- `issue_activities` -- one row per detected issue/PR change (created,
  updated, closed), recorded during the collector's upsert loop. Drives the
  admin page's "Recent Activity" feed.

Migrations are managed with Alembic (`alembic/`). Run `make migrate` or
`uv run alembic upgrade head` to apply them.

## Route structure

All routes are in `craft_dashboard/routes/`:

- `dashboard.py` -- `GET /` renders the overview page with project counts.
- `issues.py` -- `GET /issues` renders the triage table. `GET /issues/table`
  returns just the table partial for HTMX swaps. `GET /issues/{project}/{number}`
  renders an individual issue detail page. `GET /issues/export` exports filtered
  issues as CSV.
- `stats.py` -- `GET /stats` redirects to `/stats/trends`. Sub-routes for
  trends, releases, and dependencies. `GET /stats/trends/all-data` returns the
  full trend dataset as JSON for Chart.js. `GET /stats/trends/data` and
  `GET /stats/trends/chart` return per-project trend data and chart partials for
  HTMX. `GET /stats/triage` renders the triage summary page.
- `admin.py` -- `GET /admin` renders the admin page. `POST /admin/auth` and
  `POST /admin/logout` handle session login. `GET /admin/status` returns
  collection run status as JSON. `POST /admin/refresh` triggers a data refresh,
  `POST /admin/re-evaluate` triggers LLM re-evaluation, and
  `POST /admin/distribute` spreads refresh schedules evenly. `GET /admin/health`
  returns a health check. `GET /admin/logs` streams recent application logs.
  Protected by bearer token.
- `eval_api.py` -- pull-based evaluation API. `GET /api/eval/next` leases the
  next issue, `POST /api/eval/result` stores the evaluation, and
  `GET /api/eval/status` returns queue counts. Protected by the eval API token.

## Pull-based evaluation architecture

The local eval workflow is designed so the server never needs outbound access to
a developer workstation or home-lab LLM.

```
┌──────────────────────────────┐   ← HTTPS (`next` / `result`) ←   ┌────────────────────────────────┐
│ Docker container on VPS      │                                    │ Local machine                  │
│ FastAPI + /api/eval/*        │                                    │ scripts/eval_client.py         │
│ PostgreSQL                   │                                    │ OpenAI-compatible local LLM    │
└──────────────────────────────┘                                    └────────────────────────────────┘
```

Flow:

1. The server exposes `GET /api/eval/next` to hand out the next eligible issue.
2. The eval client pulls that issue over HTTPS and evaluates it locally.
3. The client pushes the finished result to `POST /api/eval/result`.
4. Operators can query `GET /api/eval/status` to see queue counts.

Security properties:

- All `/api/eval/*` endpoints require bearer token authentication via
  `EVAL_API_TOKEN`.
- The server does not initiate any connection to the developer machine.
- Local LLMs stay behind the client host; only evaluation results are sent back.
- The server uses short-lived evaluation locks so multiple clients do not work
  the same issue at once.

## Templates

Templates use Jinja2 and extend `base.html`. The base template includes the
Vanilla CSS framework from Ubuntu and htmx. Pages are in subdirectories
(`dashboard/`, `issues/`, `stats/`, `admin/`, `errors/`). Reusable parts like
pagination and score badges are in `components/`.

The issues page uses a custom multiselect widget (`static/js/multiselect.js`)
that syncs checkbox state to hidden inputs. The hidden inputs trigger HTMX
requests on change.

## Configuration

Two configuration sources:

- `craft-dashboard.toml` -- lists of projects, libraries, maintainers, bots,
  and hotfix version thresholds. Loaded at startup by `config.py`. This file
  is checked into the repo.

- Environment variables / `.env` -- database URL, API tokens, LLM backend
  settings, pool sizes. Loaded by `settings.py` via pydantic-settings.

## LLM evaluation

The LLM subsystem lives in `craft_dashboard/llm/`.

- Server-side evaluation uses OpenRouter, with
  `anthropic/claude-haiku-4.5` for evaluation and
  `google/gemini-2.5-flash-lite` for summaries by default.
- Local LLM evaluation uses the pull-based eval client; the local client class
  remains available for that workflow.

The evaluator hashes issue content and skips re-evaluation when the hash
matches the previous run. This keeps API costs low for daily cron runs.

## Refresh scheduling

Each `(project, source)` pair has a row in `refresh_schedule`. The collector
checks `next_refresh_at` before fetching; if the project is not due, it skips
the full issue fetch (but still collects dependencies and releases, which are
cheap).

After a successful collection, `next_refresh_at` is pushed forward by
`refresh_interval_days` (default 7). Failed collections increment
`consecutive_failures` and leave the schedule unchanged so the next run
retries.

The `/admin/distribute` endpoint spreads all schedules evenly across the
refresh interval so that not all projects are fetched on the same day.
