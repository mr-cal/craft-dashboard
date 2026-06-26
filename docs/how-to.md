# How-to guide

## Scripts

All scripts live in `scripts/`. In local development they run inside the Docker
Compose app container. In production they run inside the Podman container.

**Local:**
```bash
docker compose exec -T app python scripts/<script>.py
```

**Production:**
```bash
podman exec -i vps-infra_craft-dashboard_1 python scripts/<script>.py
```

### collect_data.py

Fetches issues, PRs, releases, and dependencies from GitHub and Launchpad.
Generates daily snapshots after each project's collection.

Two collection modes are used in production:

**Open-issue refresh** (`--mode open`, default): runs every 4 hours, always
refreshes all open GitHub issues for every project — no schedule gate.

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
# Open-issue refresh — every 4 hours
podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source github --mode open

# Full collection (open + closed issues, launchpad) — daily at 2 AM UTC
podman exec -i vps-infra_craft-dashboard_1 /app/.venv/bin/python /app/scripts/collect_data.py --source all --mode full
```

The full-refresh mode (`--mode full`) respects the per-project refresh schedule.
If a project is not yet due, it skips the issue fetch but still collects
dependencies and releases. To force a full refresh of all projects, see
"Force-refresh all data" below.

### run_llm.py

Server-side LLM evaluation using OpenRouter. Runs inside the app container and
writes results directly to the database.

**Prerequisites:** `OPENROUTER_API_KEY` in `.env` and `ENABLE_SERVER_EVAL=true`.

```
# evaluate all open issues (daily cron mode)
uv run scripts/run_llm.py evaluate --open-only

# evaluate everything (open + closed)
uv run scripts/run_llm.py evaluate

# limit to a project
uv run scripts/run_llm.py evaluate --project snapcraft

# limit number of issues (good for testing API costs)
uv run scripts/run_llm.py evaluate --open-only --limit 40
```

In production, this runs as a daily cron job at 6 AM UTC:

```bash
# local
docker compose exec -T app python scripts/run_llm.py evaluate --open-only
# production
podman exec -i vps-infra_craft-dashboard_1 python scripts/run_llm.py evaluate --open-only
```

The evaluator hashes issue content and skips unchanged issues, so daily runs
only process newly changed issues.

For **local LLM evaluation** (pull-based, runs on your machine), use the eval
client instead. See [`docs/eval-client.md`](eval-client.md).

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
docker compose exec -T app python scripts/collect_data.py --source all --mode all
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

### Check logs

```bash
# local
docker compose logs -f app
docker compose logs -f postgres

# production
podman logs -f vps-infra_craft-dashboard_1
podman logs -f vps-infra_postgres_1
```

### Database access

```bash
# local
docker compose exec postgres psql -U craft_dashboard craft_dashboard

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
docker compose stop app
docker compose exec postgres dropdb -U craft_dashboard craft_dashboard
docker compose exec postgres createdb -U craft_dashboard craft_dashboard
gunzip -c backup.sql.gz | docker compose exec -T postgres psql -U craft_dashboard craft_dashboard
docker compose up -d
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

For local LLM evaluation, use the pull-based eval client instead. See
[`docs/eval-client.md`](eval-client.md) for setup and usage.

### Delete all existing evaluations

Remove all LLM evaluation results to start fresh or re-evaluate everything:

```bash
# local
docker compose exec -T postgres psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM llm_evaluations;"

# production
podman exec -i vps-infra_postgres_1 psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM llm_evaluations;"
```

To delete evaluations for a specific project only:

```bash
# local
docker compose exec -T postgres psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM llm_evaluations WHERE issue_id IN (SELECT id FROM issues WHERE project_id = (SELECT id FROM projects WHERE name = 'snapcraft'));"

# production
podman exec -i vps-infra_postgres_1 psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM llm_evaluations WHERE issue_id IN (SELECT id FROM issues WHERE project_id = (SELECT id FROM projects WHERE name = 'snapcraft'));"
```
