# How-to guide

## Scripts

All scripts live in `scripts/`. In production, they run inside the app
container via `docker compose exec`. For local development, they run against
the Docker Compose PostgreSQL container (tests use SQLite and do not need
Docker).

### collect_data.py

Fetches issues, PRs, releases, and dependencies from GitHub and Launchpad.
Generates daily snapshots after each project's collection.

```
# full collection (all configured repos, all sources)
uv run scripts/collect_data.py --source all

# GitHub only
uv run scripts/collect_data.py --source github

# Launchpad only
uv run scripts/collect_data.py --source launchpad

# limit to specific projects
uv run scripts/collect_data.py --source github --project snapcraft --project rockcraft

# limit how many issues per repo (good for testing)
uv run scripts/collect_data.py --source github --limit 25

# verbose logging (individual issues, API calls)
uv run scripts/collect_data.py --source github --project snapcraft -v
```

In production, this runs as a daily cron job at 2 AM UTC via
`docker compose exec`:

```bash
docker compose exec -T app python scripts/collect_data.py --source all
```

The script respects the refresh schedule. If a project was recently fetched, it
skips the full issue fetch but still collects dependencies and releases. To
force a full refresh of all projects, see "Force-refresh all data" below.

### run_llm.py

Sends issues to an LLM for triage evaluation (summary, suggested action,
staleness score).

**Prerequisites:** The script needs a PostgreSQL database (`DATABASE_URL` in
`.env`) and an LLM backend. For `--backend openrouter`, set `OPENROUTER_API_KEY`.
For `--backend local`, a local LLM server (e.g. ollama or llama-server) must
be running at `LOCAL_LLM_URL` (default: `http://localhost:11434/v1`).

```
# evaluate all open issues (daily cron mode)
uv run scripts/run_llm.py --open-only

# evaluate everything (open + closed)
uv run scripts/run_llm.py

# limit to a project
uv run scripts/run_llm.py --project snapcraft

# limit number of issues (good for testing API costs)
uv run scripts/run_llm.py --open-only --limit 40

# use local LLM instead of OpenRouter
uv run scripts/run_llm.py --backend local
```

In production, this runs as a daily cron job at 6 AM UTC:

```bash
docker compose exec -T app python scripts/run_llm.py evaluate --open-only
```

The evaluator hashes issue content and skips unchanged issues, so daily runs
only process newly changed issues and cost very little.

### backfill_snapshots.py

One-time script that computes historical daily snapshots by replaying issue
creation and closure dates. Run this after the first data collection to
populate the trends charts with history going back to each project's earliest
issue.

```
uv run scripts/backfill_snapshots.py
```

This is a synchronous script (uses the sync SQLAlchemy engine). It reads all
issues from the database and writes snapshot rows for every day from the
earliest `created_at` to today.

It also computes cross-project aggregate snapshots (for the "all-projects"
synthetic project).

### migrate_csv.py

One-time migration script for importing data from the old starcraft-stats CSV
format. Not needed for new deployments.

```
uv run scripts/migrate_csv.py --data-dir /path/to/starcraft-stats/html/data
```

### lp_bug_report.py

Prints a report of Launchpad bug authors sorted by bug count. Useful for
identifying frequent reporters.

```
DATABASE_URL=postgresql+asyncpg://... uv run scripts/lp_bug_report.py
```

## Common operations

### Force-refresh all data

The collector skips projects that are not due for refresh. To force a full
re-fetch of everything, reset all refresh schedules by calling the admin
distribute endpoint:

```bash
curl -X POST http://localhost:8000/admin/distribute \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Origin: http://localhost:8000"
```

Then trigger a collection:

```bash
docker compose exec -T app python scripts/collect_data.py --source all
```

Alternatively, delete all refresh schedule rows. The collector treats missing
schedules as "due now":

```bash
docker compose exec -T postgres psql -U craft_dashboard craft_dashboard \
  -c "DELETE FROM refresh_schedule;"
docker compose exec -T app python scripts/collect_data.py --source all
```

### Distribute refresh schedules

The distribute endpoint spreads all project refresh times evenly across the
configured interval (default: 7 days). This prevents all projects from being
fetched on the same day, which reduces API load and collection time.

```bash
curl -X POST http://localhost:8000/admin/distribute \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Origin: http://localhost:8000"
```

### Check logs

```bash
# application logs
docker compose logs -f app

# database logs
docker compose logs -f postgres
```

### Database access

```bash
docker compose exec -T postgres psql -U craft_dashboard craft_dashboard
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

The database must be empty before restoring — the dump includes `CREATE TABLE`
statements that conflict with an already-migrated schema.

```bash
# Stop the app
docker compose stop app

# Drop and recreate the database
docker compose exec -T postgres psql -U craft_dashboard postgres \
  -c "DROP DATABASE craft_dashboard; CREATE DATABASE craft_dashboard;"

# Restore
gunzip -c backup.sql.gz | docker compose exec -T postgres psql -U craft_dashboard craft_dashboard

# Restart (Alembic sees the existing schema and skips migrations)
docker compose up -d
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
