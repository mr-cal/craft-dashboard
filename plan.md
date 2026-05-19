# craft-dashboard — Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement each sub-plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert starcraft-stats into craft-dashboard — a FastAPI + HTMX web dashboard with PostgreSQL, LLM-powered issue/PR triage, and Ansible-provisioned VPS hosting.

**Architecture:** FastAPI serves server-rendered HTML via Jinja2 + HTMX for interactivity. PostgreSQL stores all data (replacing CSV/JSON files). Data collection runs on cron jobs on the VPS. An LLM pipeline (via OpenRouter) evaluates and scores issues/PRs daily. Ansible provisions the VPS idempotently.

**Tech Stack:**
- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x (async), Alembic, Pydantic v2
- **Frontend:** Jinja2 templates, HTMX, Vanilla framework (Canonical)
- **Database:** PostgreSQL 16
- **LLM:** OpenRouter API (production) or any OpenAI-compatible local server (local development) — via OpenAI-compatible HTTP calls with `httpx`
- **Provisioning:** Ansible
- **Server:** Ubuntu 24.04 LTS, Nginx, systemd, Let's Encrypt
- **Auth:** Read-only public access; admin actions behind token-based auth

---

## Implementation Context

This plan is implemented in the renamed `craft-dashboard` repo (formerly `starcraft-stats`).
The existing `starcraft_stats/` Python package and `html/` directory remain present at the start
and can be broken or removed as new code replaces them.

**Before implementing each plan, read the corresponding existing code:**

| Plan | Existing files to read first |
|------|------------------------------|
| 01 — Scaffold | `starcraft_stats/config.py`, `pyproject.toml`, `.pre-commit-config.yaml` |
| 02 — DB models | `starcraft_stats/models/`, `starcraft_stats/models/github.py`, `starcraft_stats/models/issues.py` |
| 03 — Data collection | `starcraft_stats/issues.py` (GitHub API, pagination, field mapping), `starcraft_stats/launchpad.py` (Launchpad quirks), `starcraft_stats/dependencies.py`, `starcraft_stats/releases.py` |
| 04 — Web dashboard | `html/js/issues.js`, `html/js/index.js` (Chart.js config, filter logic), `html/index.html`, `html/issues.html`, `html/css/custom.css` |
| 05 — LLM | `starcraft_stats/issues.py` (issue field structure) |
| 06 — Provisioning | `starcraft_stats/schedule.py` (existing cron logic) |

Remove old `starcraft_stats/` and `html/` in a cleanup commit once all plans are complete.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        VPS (Ubuntu 24.04)                   │
│                                                             │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │  Nginx   │───▶│   FastAPI    │───▶│   PostgreSQL     │   │
│  │ (reverse │    │  (gunicorn)  │    │   (port 5432)    │   │
│  │  proxy)  │    │  port 8000   │    │                  │   │
│  └──────────┘    └──────────────┘    └──────────────────┘   │
│                         │                     ▲              │
│                         │                     │              │
│  ┌──────────────────────┴─────────────────────┘──────────┐  │
│  │                  Cron Jobs (systemd timers)            │  │
│  │  • collect_data.py  (hourly/daily)                    │  │
│  │  • run_llm.py       (daily, after collection)         │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              OpenRouter API (external)                 │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Sub-Plans

Each sub-plan is an independent, testable deliverable. They should be implemented in order (later plans depend on earlier ones).

| # | Plan | Description | Depends On |
|---|------|-------------|------------|
| 1 | [Project Scaffold](plans/01-project-scaffold.md) | New project structure, FastAPI skeleton, config system | — |
| 2 | [Database & Models](plans/02-database-models.md) | PostgreSQL schema, SQLAlchemy models, Alembic migrations | Plan 1 |
| 3 | [Data Collection Pipeline](plans/03-data-collection.md) | GitHub/Launchpad fetchers, snapshot generation, cron scheduling | Plans 1, 2 |
| 4 | [Web Dashboard & Frontend](plans/04-web-dashboard.md) | FastAPI routes, HTMX templates, stats views, triage dashboard, auth | Plans 1, 2 |
| 5 | [LLM Integration](plans/05-llm-integration.md) | OpenRouter client, issue/PR evaluation, scoring, summarization | Plans 1, 2, 3 |
| 6 | [Provisioning & Deployment](plans/06-provisioning.md) | Ansible playbooks, Nginx, systemd, PostgreSQL, cron, SSL, LXD VM testing | Plans 1–5 |

## Key Design Decisions

1. **No craft-application/craft-cli dependency.** The new project uses FastAPI as its core framework. CLI commands for data collection use `click` (comes with FastAPI/uvicorn).
2. **PostgreSQL with JSONB.** Structured columns for queryable fields; JSONB for flexible metadata (labels, extra source-specific data, LLM scores).
3. **HTMX for interactivity.** Server-rendered HTML with HTMX for dynamic filtering, sorting, and pagination. Minimal custom JavaScript.
4. **LLM token optimization.** Only re-evaluate issues that changed since last evaluation (tracked via content hash). Use smaller models for summarization, larger for scoring.
5. **Dual LLM backends.** `run_llm.py` supports `--backend local` (any OpenAI-compatible server) and `--backend openrouter`. Run the initial full pass locally for free, then migrate the evaluation data to the VPS via `pg_dump`/restore. Daily cron on the VPS uses OpenRouter for incremental updates.
6. **Evaluate all issues.** LLM evaluation covers both open and closed issues. Closed issues rarely change, so the hash-based deduplication ensures they're only re-evaluated if their content changes.
7. **Advisory-only actions.** LLM suggests actions (close, triage, etc.) but humans act on them manually through GitHub/Launchpad.
8. **Read-only public, admin behind auth.** Dashboard is publicly readable. Admin endpoints (force refresh, re-evaluate, etc.) require a bearer token.

## Project File Structure

```
craft-dashboard/
├── pyproject.toml
├── craft-dashboard.toml          # Project configuration
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── craft_dashboard/
│   ├── __init__.py
│   ├── app.py                    # FastAPI app factory
│   ├── config.py                 # TOML config loading
│   ├── database.py               # SQLAlchemy engine + session
│   ├── auth.py                   # Token-based auth dependency
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py               # SQLAlchemy declarative base
│   │   ├── project.py            # Project model
│   │   ├── issue.py              # Issue/PR model
│   │   ├── release.py            # Release model
│   │   ├── dependency.py         # Dependency model
│   │   ├── snapshot.py           # Daily snapshot model
│   │   └── llm_evaluation.py    # LLM evaluation model
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── github.py             # GitHub API client
│   │   ├── launchpad.py          # Launchpad API client
│   │   ├── dependencies.py       # Dependency scanner
│   │   └── releases.py           # Release fetcher
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py             # OpenRouter HTTP client
│   │   ├── prompts.py            # Prompt templates
│   │   └── evaluator.py          # Issue/PR evaluator
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py          # Main dashboard + overview
│   │   ├── issues.py             # Issue triage views
│   │   ├── stats.py              # Legacy stats (deps, releases, trends)
│   │   └── admin.py              # Admin endpoints (refresh, re-eval)
│   ├── templates/
│   │   ├── base.html
│   │   ├── components/           # Reusable HTMX partials
│   │   │   ├── issue_row.html
│   │   │   ├── filters.html
│   │   │   ├── pagination.html
│   │   │   └── score_badge.html
│   │   ├── dashboard/
│   │   │   └── index.html
│   │   ├── issues/
│   │   │   ├── list.html
│   │   │   └── detail.html
│   │   └── stats/
│   │       ├── dependencies.html
│   │       ├── releases.html
│   │       └── trends.html
│   └── static/
│       ├── css/
│       │   └── custom.css
│       └── js/
│           └── charts.js         # Chart.js for trend graphs
├── scripts/
│   ├── collect_data.py           # Cron entry point: data collection
│   ├── run_llm.py                # Cron entry point: LLM evaluation
│   └── migrate_csv.py            # One-time CSV→PostgreSQL migration
├── provisioning/
│   ├── inventory.yml
│   ├── playbook.yml
│   ├── group_vars/
│   │   └── all.yml
│   └── roles/
│       ├── common/               # Base packages, unattended upgrades
│       ├── postgresql/           # DB setup
│       ├── app/                  # App deployment, systemd, gunicorn
│       ├── nginx/                # Reverse proxy, SSL
│       └── cron/                 # Systemd timers for data collection
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_models.py
│   │   ├── test_collectors.py
│   │   ├── test_llm.py
│   │   └── test_routes.py
│   └── integration/
│       ├── test_database.py
│       └── test_api.py
├── Makefile
├── common.mk
└── README.md
```

## Database Schema

```sql
CREATE TABLE projects (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    category VARCHAR(50) NOT NULL,       -- 'application', 'library', 'other'
    github_org VARCHAR(255) DEFAULT 'canonical',
    launchpad_name VARCHAR(255),
    display_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE issues (
    id SERIAL PRIMARY KEY,
    project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source VARCHAR(20) NOT NULL,         -- 'github', 'launchpad'
    external_id VARCHAR(255) NOT NULL,   -- GitHub number or LP bug ID
    issue_type VARCHAR(20) NOT NULL,     -- 'issue', 'pull_request'
    title TEXT NOT NULL,
    body TEXT,
    state VARCHAR(20) NOT NULL,          -- 'open', 'closed', 'merged'
    author VARCHAR(255),
    author_is_maintainer BOOLEAN DEFAULT FALSE,
    labels JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    url TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    last_fetched_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, source, external_id)
);

CREATE TABLE llm_evaluations (
    id SERIAL PRIMARY KEY,
    issue_id INT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    model_name VARCHAR(255) NOT NULL,
    summary TEXT,
    suggested_action VARCHAR(50),        -- 'close_stale', 'close_duplicate', etc.
    suggested_action_reason TEXT,
    scores JSONB DEFAULT '{}'::jsonb,    -- {"staleness": 85, "readiness": 20, ...}
    tokens_used INT,
    evaluated_at TIMESTAMPTZ DEFAULT NOW(),
    issue_data_hash VARCHAR(64),         -- SHA-256 of issue content for change detection
    latest BOOLEAN NOT NULL DEFAULT TRUE -- marks the most recent evaluation for this issue
    -- Partial unique index ensures only one 'latest=true' row per issue:
    -- CREATE UNIQUE INDEX ON llm_evaluations (issue_id) WHERE latest = true;
);

CREATE TABLE snapshots (
    id SERIAL PRIMARY KEY,
    project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    open_issues INT DEFAULT 0,
    open_prs INT DEFAULT 0,
    open_issues_external INT DEFAULT 0,
    open_issues_internal INT DEFAULT 0,
    open_prs_external INT DEFAULT 0,
    open_prs_internal INT DEFAULT 0,
    open_bugs INT DEFAULT 0,
    UNIQUE(project_id, snapshot_date)
);

CREATE TABLE releases (
    id SERIAL PRIMARY KEY,
    project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version VARCHAR(100) NOT NULL,
    branch VARCHAR(255),
    released_at TIMESTAMPTZ,
    is_hotfix BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}'::jsonb,
    UNIQUE(project_id, version)
);

CREATE TABLE dependencies (
    id SERIAL PRIMARY KEY,
    project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    branch VARCHAR(255) NOT NULL,
    dependency_name VARCHAR(255) NOT NULL,
    version_spec VARCHAR(255),
    source_file VARCHAR(255),
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, branch, dependency_name)
);

CREATE TABLE refresh_schedule (
    id SERIAL PRIMARY KEY,
    project_id INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source VARCHAR(20) NOT NULL,         -- 'github', 'launchpad'
    next_refresh_at TIMESTAMPTZ,
    last_refreshed_at TIMESTAMPTZ,
    last_error TEXT,                     -- error message from last failed collection
    consecutive_failures INT DEFAULT 0, -- number of consecutive collection failures
    UNIQUE(project_id, source)
);
```

## LLM Scoring Dimensions

Each issue/PR gets evaluated on these dimensions (0–100 scale):

| Score | Description | Applies To |
|-------|-------------|------------|
| `staleness` | How stale is this? Based on last activity, age, no recent comments | Issues, PRs |
| `readiness` | How ready is this PR for review/merge? Tests passing, approvals, no conflicts | PRs only |
| `relevance` | Is this still relevant to the project? | Issues, PRs |
| `duplicateness` | How likely is this a duplicate of another issue? | Issues |
| `support_request` | Is this a support/help request rather than a bug/feature? | Issues |
| `complexity` | How complex is this issue/PR? | Issues, PRs |

### Suggested Actions

| Action | Description |
|--------|-------------|
| `close_stale` | Close because it's been inactive too long |
| `close_duplicate` | Close as duplicate (with reference to original) |
| `close_not_a_bug` | Close because it's a support request, not a bug |
| `close_outdated` | Close because it's no longer relevant |
| `needs_triage` | Needs human attention to classify/prioritize |
| `needs_review` | PR is ready for review |
| `needs_rebase` | PR needs rebasing |
| `keep_open` | No action needed, issue/PR is healthy |
