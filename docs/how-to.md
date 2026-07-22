# How-to guide

## Scripts

All scripts live in `scripts/`. In local development they run inside the Podman
Compose app container. In production they run inside the Podman container.

**Local:**
```bash
podman compose exec -T app python scripts/<script>.py
```

**Production:**
```bash
podman exec -i vps-infra_craft-dashboard_1 python scripts/<script>.py
```

### collect_data.py

Fetches issues, PRs, releases, and dependencies from GitHub and Launchpad.
Generates daily snapshots after each project's collection.

Two collection modes are used in production:

**Open-issue refresh** (`--mode open`, default): runs every 10 minutes, always
refreshes all open GitHub issues (and releases) for every project — no schedule gate.

**Full refresh** (`--mode full`): runs once daily, refreshes all issues
(open + closed) per the per-project schedule (`refresh-interval-days` in
`craft-dashboard.toml`, default 7 days). Also collects Launchpad bugs.

```
# open-issue refresh (default mode)
uv run scripts/collect_data.py --source github --mode open

# full collection (all configured repos, all sources, per-project schedule)
uv run scripts/collect_data.py --source all --mode full

# force full collection for all projects, ignore schedule
uv run scripts/collect_data.py --source all --mode all

# GitHub only
uv run scripts/collect_data.py --source github --mode full

# Launchpad only
uv run scripts/collect_data.py --source launchpad

# limit to specific projects
uv run scripts/collect_data.py --source github --project snapcraft --project rockcraft

# limit how many issues per repo (good for testing)
uv run scripts/collect_data.py --source github --limit 25

# verbose logging (individual issues, API calls)
uv run scripts/collect_data.py --source github --project snapcraft -v
```

In production, two cron jobs run:

```bash
# Open-issue and release refresh — every 10 minutes
podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source github --mode open

# Full collection (open + closed issues, launchpad) — daily at 2 AM UTC
podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source all --mode full
```

The full-refresh mode (`--mode full`) respects the per-project refresh schedule.
If a project is not yet due, it skips the issue fetch but still collects
dependencies and releases. To force a full refresh of all projects, see
"Force-refresh all data" below.

### run_llm.py

Server-side LLM evaluation using OpenRouter. The `evaluate` entrypoint now runs
as a continuous HTTP-polling service rather than a direct-DB batch job.

**Prerequisites:** `OPENROUTER_API_KEY` in `.env`. In production, `.env` lives
at `/opt/vps-infra/.env` on the VPS (see "Reloading .env in production" in
[`docs/deployment.md`](deployment.md) for how to edit it and apply changes)
— **after editing it you must restart the app container**
(`podman restart vps-infra_craft-dashboard_1`) for changes like
`OPENROUTER_MODEL` to take effect; pydantic-settings only reads `.env` at
process startup, so re-running `run_llm.py` against a container that hasn't
been restarted will still see the old values.

The `scripts/` directory isn't a bind mount on the VPS — it's baked into the
Docker image at build time and only exists at `/app/scripts` inside the
`vps-infra_craft-dashboard_1` container. Always run it via `podman exec`
(see the production example below), not as a host path.

The model is set via the `OPENROUTER_MODEL` env var (default
`google/gemini-2.5-flash-lite`) — there is no `--model` CLI flag. Pick any
model slug from [openrouter.ai/models](https://openrouter.ai/models) (the
site lists per-token pricing and context length for each); if you change it
to something without pricing metadata, the cost estimate may be unavailable
rather than silently showing $0.

```
# start the continuous evaluation service locally
uv run scripts/run_llm.py evaluate

# start the same service inside the app container
podman compose exec -T app python scripts/run_llm.py evaluate
```

The service polls over HTTP for work, evaluates issues with OpenRouter, and
posts results back to the server. It replaces the old manual/admin-triggered
one-shot batch flow.

The evaluator hashes issue content and skips unchanged issues, so the
continuous service only processes newly changed issues.

For **local LLM evaluation** (pull-based, runs on your machine), use the eval
HTTP evaluate worker instead. See [`docs/evaluate.md`](evaluate.md).

### backfill_snapshots.py

One-time script that computes historical daily snapshots by replaying issue
creation and closure dates. Run this after the first data collection to
populate the trends charts with history going back to each project's earliest
issue.

Run it inside the production container, where `DATABASE_URL` is already set:

```bash
podman exec -i vps-infra_craft-dashboard_1 python scripts/backfill_snapshots.py
```

The script reads all issues from the database and writes snapshot rows for
every day from the earliest `created_at` to today. It also computes
cross-project aggregate snapshots for the "all-projects" synthetic project.

### lp_bug_report.py

Prints a report of Launchpad bug authors sorted by bug count. Useful for
identifying frequent reporters.

```
DATABASE_URL=postgresql+asyncpg://... uv run scripts/lp_bug_report.py
```

## Common operations

### Force-refresh all data

The full-refresh collector (`--mode full`) skips projects not yet due. To force
a re-fetch of everything regardless of schedule, use `--mode all`:

```bash
# local
podman compose exec -T app python scripts/collect_data.py --source all --mode all
# production
podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source all --mode all
```

Alternatively, reset all refresh schedules via the admin endpoint, then run
a `--mode full` collection:

```bash
curl -X POST https://craft-dashboard.name/admin/distribute \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Origin: https://craft-dashboard.name"
```

### Distribute refresh schedules

The distribute endpoint spreads all project refresh times evenly across the
configured interval (default: 7 days).

```bash
curl -X POST https://craft-dashboard.name/admin/distribute \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Origin: https://craft-dashboard.name"
```

### Update .env in production

pydantic-settings reads `.env` at startup. After editing `/opt/vps-infra/.env`
on the server, redeploy by re-running the
[vps-infra deploy workflow](https://github.com/mr-cal/vps-infra/actions/workflows/deploy.yml)
on GitHub (click **Run workflow** → **Run workflow**). This pulls the latest
image and restarts the container with the new environment.

Alternatively, restart the container directly on the server:

```bash
nano /opt/vps-infra/.env
podman restart vps-infra_craft-dashboard_1
```

The container restarts in a few seconds; Alembic migrations run on startup
but skip if the schema is already current.

### Check logs

```bash
# local
podman compose logs -f app
podman compose logs -f postgres

# production
podman logs -f vps-infra_craft-dashboard_1
podman logs -f vps-infra_postgres_1
```

### Database access

```bash
# local
podman compose exec postgres psql -U craft_dashboard craft_dashboard

# production
podman exec -it vps-infra_postgres_1 psql -U craft_dashboard craft_dashboard
```

Some useful queries:

```sql
-- check how many issues each project has
SELECT p.name, COUNT(*) FROM issues i
JOIN projects p ON i.project_id = p.id
GROUP BY p.name ORDER BY count DESC;

-- check refresh schedule status
SELECT p.name, rs.source, rs.next_refresh_at, rs.consecutive_failures
FROM refresh_schedule rs
JOIN projects p ON rs.project_id = p.id
ORDER BY rs.next_refresh_at;

-- check LLM evaluation coverage
SELECT p.name,
  COUNT(DISTINCT CASE WHEN e.id IS NOT NULL THEN i.id END) as evaluated,
  COUNT(i.id) as total
FROM issues i
JOIN projects p ON i.project_id = p.id
LEFT JOIN llm_evaluations e ON e.issue_id = i.id AND e.latest = true
WHERE i.state = 'open'
GROUP BY p.name;
```

### Restore from backup

**Local:**

```bash
podman compose stop app
podman compose exec postgres dropdb -U craft_dashboard craft_dashboard
podman compose exec postgres createdb -U craft_dashboard craft_dashboard
gunzip -c backup.sql.gz | podman compose exec -T postgres psql -U craft_dashboard craft_dashboard
podman compose up -d
```

**Production:**

```bash
podman stop vps-infra_craft-dashboard_1
podman exec -i vps-infra_postgres_1 dropdb -U craft_dashboard craft_dashboard
podman exec -i vps-infra_postgres_1 createdb -U craft_dashboard craft_dashboard
gunzip -c backup.sql.gz | podman exec -i vps-infra_postgres_1 psql -U craft_dashboard craft_dashboard
podman rm vps-infra_craft-dashboard_1
cd /opt/vps-infra && podman-compose -f docker-compose.craft-dashboard.yml up -d
```

### Add a new project

1. Add the project name to `craft-dashboard.toml` in the appropriate list
   (`craft-applications`, `craft-libraries`, or `craft-projects`).
2. If it's an application with hotfix branches, add a minimum version under
   `[hotfix-min-versions]`.
3. Push the change and deploy the updated image.
4. Run data collection to populate the project immediately, or wait for the
   next scheduled run.

### Run local LLM evaluation

The server now uses OpenRouter for server-side evaluation.

For local LLM evaluation, use `scripts/run_llm.py evaluate --llm-backend local`.
See [`docs/evaluate.md`](evaluate.md) for setup and usage.

### Delete all existing evaluations

Remove all LLM evaluation results to start fresh or re-evaluate everything:

```bash
# local
podman compose exec -T postgres psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM llm_evaluations;"

# production
podman exec -i vps-infra_postgres_1 psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM llm_evaluations;"
```

To delete evaluations for a specific project only:

```bash
# local
podman compose exec -T postgres psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM llm_evaluations WHERE issue_id IN (SELECT id FROM issues WHERE project_id = (SELECT id FROM projects WHERE name = 'snapcraft'));"

# production
podman exec -i vps-infra_postgres_1 psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM llm_evaluations WHERE issue_id IN (SELECT id FROM issues WHERE project_id = (SELECT id FROM projects WHERE name = 'snapcraft'));"
```
