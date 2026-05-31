# Architecture

## Overview

craft-dashboard is a read-only web application backed by a PostgreSQL database.
The web app never calls external APIs during request handling. All external data
(GitHub issues, Launchpad bugs, LLM evaluations) is fetched by offline scripts
that run on systemd timers.

```
                          ┌──────────────────────────────────┐
                          │  cron / docker compose exec      │
                          │                                  │
  GitHub API  <───────────│  collect_data.py  (2 AM daily)   │───────> PostgreSQL
  Launchpad   <───────────│                                  │
                          │  run_llm.py       (6 AM daily)   │───────> PostgreSQL
  OpenRouter / local LLM <│                                  │
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

2. `run_llm.py` reads issues from the database, sends them to an LLM
   (OpenRouter or a local server), and writes the evaluation (summary,
   suggested action, scores) back. It uses content hashing to skip issues
   that haven't changed since their last evaluation.

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

Migrations are managed with Alembic (`alembic/`). Run `make migrate` or
`uv run alembic upgrade head` to apply them.

## Route structure

All routes are in `craft_dashboard/routes/`:

- `dashboard.py` -- `GET /` renders the overview page with project counts.
- `issues.py` -- `GET /issues` renders the triage table. `GET /issues/table`
  returns just the table partial for HTMX swaps.
- `stats.py` -- `GET /stats` redirects to `/stats/trends`. Sub-routes for
  trends, releases, and dependencies. The `GET /stats/trends/all-data`
  endpoint returns the full trend dataset as JSON for Chart.js.
- `admin.py` -- `GET /admin` renders the admin page. POST endpoints for
  triggering refreshes and distributing schedules. Protected by bearer token.

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

The LLM subsystem lives in `craft_dashboard/llm/`. It supports two backends:

- OpenRouter (production) -- uses `anthropic/claude-haiku-4.5` for evaluation
  and `google/gemini-2.5-flash-lite` for summaries by default.
- Local LLM -- any OpenAI-compatible server (e.g. Ollama). Configure with
  `LLM_BACKEND=local` and `LOCAL_LLM_URL`.

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
