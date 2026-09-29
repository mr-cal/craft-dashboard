# Operations

All commands state their working directory. Set these shell variables once before running production recipes:

```bash
export REPO=<path-to-your-craft-dashboard-checkout>
export VPS_HOST=<ssh-user-and-host>
export VPS_WORKDIR=/opt/vps-infra
export APP_CONTAINER=vps-infra_craft-dashboard_1
export DB_CONTAINER=vps-infra_postgres_1
export APP_PYTHON=/app/.venv/bin/python
export DB_USER=craft_dashboard
export DB_NAME=craft_dashboard
export DASHBOARD_URL=https://craft-dashboard.name
export ADMIN_TOKEN=<admin-token>
export EVAL_API_TOKEN=<eval-api-token>
export BACKUP_FILE=<path-to-backup.sql.gz>
export VPS_INFRA=<path-to-your-vps-infra-checkout>
```

Use the live values from `.env.llm`, `.env`, or the VPS environment. Do not paste hosts, IP addresses, or container names into commands; update the variables.

## Collect GitHub and Launchpad data

Working directory: `$REPO`.

Local open-issue refresh:

```bash
cd "$REPO"
podman compose exec -T app python scripts/collect_data.py --source github --mode open
```

Local full collection for due projects:

```bash
cd "$REPO"
podman compose exec -T app python scripts/collect_data.py --source all --mode full
```

Local forced collection for configured projects:

```bash
cd "$REPO"
podman compose exec -T app python scripts/collect_data.py --source all --mode all
```

Production open-issue refresh:

```bash
ssh "$VPS_HOST" "podman exec -i $APP_CONTAINER $APP_PYTHON /app/scripts/collect_data.py --source github --mode open"
```

Production rotation refresh:

```bash
ssh "$VPS_HOST" "podman exec -i $APP_CONTAINER $APP_PYTHON /app/scripts/collect_data.py --mode rotation"
```

Useful filters:

```bash
cd "$REPO"
uv run scripts/collect_data.py --source github --project snapcraft --project rockcraft
uv run scripts/collect_data.py --source github --limit 25 --project snapcraft -v
```

See [reference.md](reference.md#data-collection) for all collector flags.

## Collect forum activity

Working directory: `$REPO`.

Local combined backfill and refresh:

```bash
cd "$REPO"
podman compose exec -T app python scripts/collect_forum_data.py --mode all
```

Production combined backfill and refresh:

```bash
ssh "$VPS_HOST" "podman exec -i $APP_CONTAINER $APP_PYTHON /app/scripts/collect_forum_data.py --mode all"
```

Limit a run to one forum:

```bash
cd "$REPO"
uv run scripts/collect_forum_data.py --mode backfill --forum snapcraft -v
```

The collector implementation documents the Discourse endpoint choices in `craft_dashboard/collectors/forum.py`.

## Run LLM evaluation

Working directory: `$REPO`.

Start the HTTP polling worker with configured environment variables:

```bash
cd "$REPO"
uv run scripts/run_llm.py evaluate
```

Run a bounded batch:

```bash
cd "$REPO"
uv run scripts/run_llm.py evaluate --project snapcraft --limit 10
```

Run one issue:

```bash
cd "$REPO"
uv run scripts/run_llm.py evaluate --project snapcraft --issue 1068
```

Run with a local OpenAI-compatible backend:

```bash
cd "$REPO"
uv run scripts/run_llm.py evaluate --llm-backend local
```

Production worker invocation:

```bash
ssh "$VPS_HOST" "podman exec -i $APP_CONTAINER $APP_PYTHON /app/scripts/run_llm.py evaluate"
```

Check queue status:

```bash
curl -fsS "$DASHBOARD_URL/api/eval/status"   -H "Authorization: Bearer $EVAL_API_TOKEN"
```

See [reference.md](reference.md#llm-evaluation) for flags and required environment variables.

## Staged evaluation rollout

Working directory: `$REPO`.

1. Evaluate explicit canary issues without changing version constants:

   ```bash
   cd "$REPO"
   uv run scripts/llm/canary.py      --server "$DASHBOARD_URL"      --token "$EVAL_API_TOKEN"      --issue snapcraft:6381      --issue craft-parts:766
   ```

2. Review the generated issue detail pages in the dashboard.
3. Update the relevant evaluation version constant in `craft_dashboard/llm/evaluator.py` as part of the code change being rolled out.
4. Deploy the change.
5. Run a bounded worker batch:

   ```bash
   cd "$REPO"
   uv run scripts/run_llm.py evaluate --limit 50
   ```

6. Review evaluation quality, cost, queue depth, and errors in `/admin/evaluations`.
7. Run the continuous worker without `--limit` when the batch looks correct.

To stop a rollout, stop the worker, revert the code change that changed the version constant, deploy, and demote any bad `llm_evaluations.latest` rows only after identifying their replacement rows.

## Restore a database backup

Working directory for local restore: `$REPO`.

```bash
cd "$REPO"
podman compose stop app
podman compose exec postgres dropdb -U "$DB_USER" "$DB_NAME"
podman compose exec postgres createdb -U "$DB_USER" "$DB_NAME"
gunzip -c "$BACKUP_FILE" | podman compose exec -T postgres psql -U "$DB_USER" "$DB_NAME"
podman compose up -d
```

Working directory for production restore: any directory containing `$BACKUP_FILE`.

```bash
ssh "$VPS_HOST" "podman stop $APP_CONTAINER"
ssh "$VPS_HOST" "podman exec -i $DB_CONTAINER dropdb -U $DB_USER $DB_NAME"
ssh "$VPS_HOST" "podman exec -i $DB_CONTAINER createdb -U $DB_USER $DB_NAME"
gunzip -c "$BACKUP_FILE" | ssh "$VPS_HOST" "podman exec -i $DB_CONTAINER psql -U $DB_USER $DB_NAME"
ssh "$VPS_HOST" "cd $VPS_WORKDIR && podman-compose -f docker-compose.craft-dashboard.yml up -d"
```

## Export a database backup

Working directory: `$REPO` for local backup, any directory for production backup.

```bash
cd "$REPO"
podman compose exec -T postgres pg_dump -U "$DB_USER" "$DB_NAME" | gzip > craft-dashboard-backup.sql.gz
```

```bash
ssh "$VPS_HOST" "podman exec -i $DB_CONTAINER pg_dump -U $DB_USER $DB_NAME" | gzip > craft-dashboard-backup.sql.gz
```

## Add a project

Working directory: `$REPO`.

1. Edit `craft-dashboard.toml`.
2. Add the repository name to the appropriate project list.
3. Add `hotfix-min-versions` only for applications with hotfix branches that need release tracking.
4. Run checks:

   ```bash
   cd "$REPO"
   make format
   make lint
   make test
   ```

5. Deploy the change.
6. Populate data:

   ```bash
   cd "$REPO"
   uv run scripts/collect_data.py --source github --project <project-name> --mode all
   ```

## Sync git mirrors

Working directory: `$REPO`.

```bash
cd "$REPO"
uv run craft-dashboard mirrors sync
```

Production:

```bash
ssh "$VPS_HOST" "podman exec -i $APP_CONTAINER craft-dashboard mirrors sync"
```

The command clones missing bare mirrors and fetches existing mirrors under `CRAFT_DASHBOARD_MIRROR_DIR`.

## Deploy

Working directory: `$REPO`.

```bash
cd "$REPO"
git status --short
make format
make lint
make test
git push "$("$VPS_INFRA"/scripts/mint_bot_token.py --print-remote-url)" main
```

The `publish.yml` workflow builds and pushes `ghcr.io/mr-cal/craft-dashboard:latest`, then dispatches the `craft-dashboard-updated` event to `mr-cal/vps-infra`.

Verify the image publish workflow:

```bash
gh run list --workflow publish.yml --limit 5
gh run watch --exit-status
```

Verify the VPS deploy workflow from a checkout of `mr-cal/vps-infra`:

```bash
gh run list --workflow deploy.yml --limit 5
gh run watch --exit-status
```

Verify the production site:

```bash
curl -fsS "$DASHBOARD_URL/health"
```

## Logs and database shell

Local logs from `$REPO`:

```bash
cd "$REPO"
podman compose logs -f app
podman compose logs -f postgres
```

Production logs:

```bash
ssh "$VPS_HOST" "podman logs -f $APP_CONTAINER"
ssh "$VPS_HOST" "podman logs -f $DB_CONTAINER"
```

Local database shell from `$REPO`:

```bash
cd "$REPO"
podman compose exec postgres psql -U "$DB_USER" "$DB_NAME"
```

Production database shell:

```bash
ssh -t "$VPS_HOST" "podman exec -it $DB_CONTAINER psql -U $DB_USER $DB_NAME"
```
