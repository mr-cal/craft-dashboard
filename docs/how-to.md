# How-to guide

## Scripts

All scripts live in `scripts/` and are meant to run on the server (inside the
VM or VPS). They can also run locally, but they need a real PostgreSQL database
(unlike the test suite, which uses SQLite). Set `DATABASE_URL` in your `.env`
pointing at a local or remote Postgres instance. Tests do not need this.

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

On the server, this runs as a systemd oneshot service (`collect-data`) triggered
by a daily timer at 2 AM UTC.

To run it manually on the server:

```fish
lxc exec $VM_NAME -- sudo systemctl restart collect-data
lxc exec $VM_NAME -- sudo journalctl -u collect-data -f
```

Or run the script directly:

```fish
lxc exec $VM_NAME -- sudo -u craft-dashboard bash -c \
  'cd /opt/craft-dashboard && source .env && \
   .venv/bin/python scripts/collect_data.py --source github --project snapcraft -v'
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

The easiest way to run this is on the server where the database is already set
up (see "run directly on the server" below). For local runs, open an SSH tunnel
to the server's database and set `DATABASE_URL` in your `.env`:

```fish
# In one terminal: forward the server's Postgres port to localhost:5432
ssh -L 5432:localhost:5432 $DASHBOARD_USER@$DASHBOARD_HOST

# For LXD VM: lxc exec creates a shell, so tunnel via the VM's IP instead
# lxc exec $VM_NAME -- cat /opt/craft-dashboard/.env | grep DATABASE_URL
```

The DB username is `craft_dashboard` and the password is `DB_PASSWORD` from
`provisioning/secrets.env`. Add this to your `.env`:

```
DATABASE_URL=postgresql+asyncpg://craft_dashboard:<DB_PASSWORD>@localhost/craft_dashboard
```

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

On the server, this runs as the `run-llm` systemd service at 6 AM UTC (4 hours
after collection so the data is fresh).

To run it manually on the server:

```fish
lxc exec $VM_NAME -- sudo systemctl restart run-llm
lxc exec $VM_NAME -- sudo journalctl -u run-llm -f
```

Or run the script directly on the server:

```fish
lxc exec $VM_NAME -- sudo -u craft-dashboard bash -c \
  'cd /opt/craft-dashboard && source .env && \
   .venv/bin/python scripts/run_llm.py --open-only --limit 40 -v'
```

To view cron job logs, see [Check logs](#check-logs) below.

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
distribute endpoint. This spreads the schedules evenly starting from now:

```fish
# via the admin API (requires ADMIN_TOKEN)
curl -X POST http://$VM_IP/admin/distribute \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Origin: http://$VM_IP"
```

Then restart the collector:

```fish
lxc exec $VM_NAME -- sudo systemctl restart collect-data
```

Alternatively, delete all refresh schedule rows. The collector treats missing
schedules as "due now":

```fish
lxc exec $VM_NAME -- sudo -u postgres psql craft_dashboard \
  -c "DELETE FROM refresh_schedule;"
lxc exec $VM_NAME -- sudo systemctl restart collect-data
```

### Distribute refresh schedules

The distribute endpoint spreads all project refresh times evenly across the
configured interval (default: 7 days). This prevents all projects from being
fetched on the same day, which reduces API load and collection time.

```fish
curl -X POST http://$VM_IP/admin/distribute \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Origin: http://$VM_IP"
```

### Check logs

```fish
# application logs
lxc exec $VM_NAME -- journalctl -u craft-dashboard -f

# data collection logs
lxc exec $VM_NAME -- journalctl -u collect-data -f

# LLM evaluation logs
lxc exec $VM_NAME -- journalctl -u run-llm -f

# list all timers and when they last/next fire
lxc exec $VM_NAME -- systemctl list-timers
```

For a VPS, replace `lxc exec $VM_NAME --` with `ssh -l $DASHBOARD_USER $DASHBOARD_HOST`.

### Database access

```fish
lxc exec $VM_NAME -- sudo -u postgres psql craft_dashboard
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

Daily backups are written to `/opt/craft-dashboard/backups/` by a systemd timer
(3 AM UTC). To restore:

```fish
# download the backup
scp $DASHBOARD_USER@$DASHBOARD_HOST:/opt/craft-dashboard/backups/craft-dashboard-$(date +%Y%m%d).sql.gz ~/

# stop the app, restore, restart
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl stop craft-dashboard"
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "gunzip -c ~/craft-dashboard-*.sql.gz | sudo -u postgres psql craft_dashboard"
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl restart craft-dashboard"
```

### Add a new project

1. Add the project name to `craft-dashboard.toml` in the appropriate list
   (`craft-applications`, `craft-libraries`, or `craft-projects`).
2. If it's an application with hotfix branches, add a minimum version under
   `[hotfix-min-versions]`.
3. Push the change and re-deploy (`make deploy-vm` or `make deploy`).
4. Run data collection to populate the project immediately, or wait for the
   next scheduled run.

### Switch LLM backend

To use a local LLM server instead of OpenRouter, set these in
`provisioning/secrets.env`:

```
LLM_BACKEND=local
LOCAL_LLM_URL=http://192.168.1.10:11434/v1
LOCAL_LLM_SUMMARY_MODEL=qwen2.5
LOCAL_LLM_EVALUATION_MODEL=qwen2.5
```

Then re-deploy. The local server needs to be reachable from the VM/VPS.
