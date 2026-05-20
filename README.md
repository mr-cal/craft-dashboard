# craft-dashboard

Dashboard, insights, and issue triage for the \*craft applications and libraries.

## Overview

craft-dashboard is a web application that collects GitHub and Launchpad data, runs
LLM-based triage on open issues, and presents the results as a filterable dashboard.
The main features are:

- issue and PR triage with LLM-powered scoring and suggested actions
- statistics and trends for open issues, PRs, releases, and dependencies
- data from GitHub and Launchpad

---

## Architecture

The application runs inside a VM or VPS. All the moving parts are installed and
configured by Ansible. You never run the app directly on your local machine.

```
internet or local network
         |
    nginx :80 (dev VM) or :80/:443 (VPS)
         |
    gunicorn 127.0.0.1:8000 (4 workers)
         |
    FastAPI (craft_dashboard/)
         |
    PostgreSQL (localhost/craft_dashboard)
```

Scheduled jobs run as systemd one-shot services triggered by timers:

```
collect-data.timer   daily 2 AM    collect-data.service -> collect_data.py --source all
run-llm.timer        daily 6 AM    run-llm.service      -> run_llm.py --open-only
backup-db.timer      daily         backup-db.service    -> pg_dump to backups/
```

### Server file layout

```
/opt/craft-dashboard/          application root, owned by the craft-dashboard user
  .env                         environment variables (secrets, not in git)
  .venv/                       Python virtual environment
  craft-dashboard.toml         project config: repos, maintainers, schedules
  backups/                     daily pg_dump backups
  craft_dashboard/             Python package (routes, models, collectors, LLM)
  scripts/                     data collection and LLM evaluation scripts
  alembic/                     database migration files

/etc/systemd/system/
  craft-dashboard.service      web app (gunicorn)
  collect-data.service/.timer  daily GitHub and Launchpad collection
  run-llm.service/.timer       daily LLM evaluation of open issues
  backup-db.service/.timer     daily database backup

/etc/nginx/sites-available/craft-dashboard   reverse proxy config
```

### How deployments work

Both `make deploy` and `make deploy-vm` run on your **local machine**. You do not
need to check out the project on the VM or VPS. Ansible SSHes in, pulls the
application from GitHub, installs dependencies, runs migrations, and restarts the
service.

The repo and branch are configured in `provisioning/group_vars/all.yml`:

```yaml
app_repo:   https://github.com/mr-cal/craft-dashboard.git
app_branch: main
```

To deploy code changes:

1. Push the changes to the configured branch on GitHub.
2. Re-run `make deploy-vm` (dev) or `make deploy` (production).

Both commands are idempotent and safe to re-run. Re-running `make deploy-vm` after
a code push is the normal workflow during development.

---

## 1. Local Prerequisites

You only need these on your local machine:

- uv (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Ansible 2.16+: `uv tool install ansible-core`
- git

PostgreSQL, Python, and nginx run inside the LXD VM. You do not need them locally.

Ansible also needs two Galaxy collections. `make deploy-vm` installs them
automatically, or install manually:

```fish
ansible-galaxy collection install -r provisioning/requirements.yml
```

---

## 2. Linting and Unit Tests

Unit tests use mocks and do not require a running database:

```fish
make test   # run unit tests
make lint   # run linter
```

---

## 3. Setting Up an LXD VM

Development happens inside an LXD VM. This matches the production VPS environment:
the same systemd services, the same nginx config, and the same database.

LXD containers do not support systemd reliably, so the `--vm` flag is required.

### 3a. Create a VM

```fish
lxc launch ubuntu:24.04 craft-dashboard-dev --vm
# The VM agent takes a moment to start
sleep 30 && lxc exec craft-dashboard-dev -- cloud-init status --wait
```

If you use [local-llm](https://github.com/mr-cal/local-llm), it renames the VM
user to match your host username. Standard `lxc launch` keeps the user as `ubuntu`.

### 3b. Generate an SSH key (if needed)

Ansible connects over SSH. If `~/.ssh/id_ed25519` does not exist yet:

```fish
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
```

### 3c. Configure secrets

```fish
cp provisioning/secrets.env.example provisioning/secrets.env
```

Edit `provisioning/secrets.env`. Minimum required values:

```
VM_NAME=craft-dashboard-dev         # LXD VM name (used in lxc commands)
DASHBOARD_USER=ubuntu               # VM SSH user (your host username if using local-llm)
DOMAIN_NAME=localhost
DB_PASSWORD=dev-password-123
GITHUB_TOKEN=<your GitHub fine-grained token>
```

`provisioning/secrets.env` is gitignored and never committed.

### 3d. Load VM variables

The VM may have multiple network interfaces. Parse the IP with grep:

```fish
set VM_NAME (grep '^VM_NAME=' provisioning/secrets.env | cut -d= -f2)
set VM_USER (grep '^DASHBOARD_USER=' provisioning/secrets.env | cut -d= -f2)
set VM_IP (lxc list $VM_NAME -c4 --format csv | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "VM: $VM_NAME  user: $VM_USER  IP: $VM_IP"
```

Update `DASHBOARD_HOST` in `provisioning/secrets.env` with this IP. Re-run this
block whenever the VM restarts, as LXD may assign a different IP.

### 3e. Set up SSH access

```fish
lxc exec $VM_NAME -- mkdir -p /home/$VM_USER/.ssh
lxc file push ~/.ssh/id_ed25519.pub $VM_NAME/home/$VM_USER/.ssh/authorized_keys
lxc exec $VM_NAME -- chown -R $VM_USER:$VM_USER /home/$VM_USER/.ssh
lxc exec $VM_NAME -- chmod 600 /home/$VM_USER/.ssh/authorized_keys
ssh -o StrictHostKeyChecking=no -l $VM_USER $VM_IP "echo SSH works"
```

Ansible passes the username as `-o User=...` internally, so usernames containing
`@` work without any workarounds.

### 3f. Provision the VM

```fish
make deploy-vm
```

This runs Ansible from your local machine. It SSHes into the VM and installs
PostgreSQL, the app, nginx, and the systemd timers. It also runs database
migrations to create the schema.

### 3g. Access the dashboard

After provisioning, the dashboard is available at:

```
http://$VM_IP/
```

Port 80, plain HTTP. The dev VM does not use SSL (Ansible skips certbot when run
with `--skip-tags ssl`, which `make deploy-vm` does). Gunicorn binds to
`127.0.0.1:8000` inside the VM and is not directly reachable from outside; nginx
proxies all traffic on port 80 to it.

Do not use `https://`, port 8000, or `localhost` — none of those work for the VM.

---

## 4. Bootstrapping Data (First Time)

After provisioning, collect data and run LLM evaluation. All commands below run
on your local machine via `lxc exec`.

### Step 1: Collect data

For a full collection across all 18 configured repos (takes 20-40 minutes):

```fish
lxc exec $VM_NAME -- sudo systemctl start collect-data
lxc exec $VM_NAME -- sudo journalctl -u collect-data -f
```

The script logs a progress line every 25 issues so you can see it is working.

For faster testing, use `--limit` (max issues per repo) and `--project` (filter repos):

```fish
lxc exec $VM_NAME -- sudo -u craft-dashboard bash -c \
  'cd /opt/craft-dashboard && source .env && \
   .venv/bin/python scripts/collect_data.py \
   --source github --limit 25 --project snapcraft --project rockcraft'
```

### Step 2: Run LLM evaluation

```fish
# Test run (40 issues, uses a small amount of OpenRouter credit):
lxc exec $VM_NAME -- sudo -u craft-dashboard bash -c \
  'cd /opt/craft-dashboard && source .env && \
   .venv/bin/python scripts/run_llm.py --open-only --limit 40'

# Full pass over all open issues:
lxc exec $VM_NAME -- sudo -u craft-dashboard bash -c \
  'cd /opt/craft-dashboard && source .env && \
   .venv/bin/python scripts/run_llm.py --open-only'
```

After the first pass, the daily cron only processes newly-changed open issues,
so OpenRouter costs stay low.

### Verbosity

Both scripts accept `-v` / `--verbose` for debug-level logging (individual issues,
API calls, LLM token counts):

```fish
lxc exec $VM_NAME -- sudo -u craft-dashboard bash -c \
  'cd /opt/craft-dashboard && source .env && \
   .venv/bin/python scripts/collect_data.py --source github --project snapcraft -v'
```

To increase verbosity for the systemd services without changing service files, set
`LOG_LEVEL=DEBUG` in `/opt/craft-dashboard/.env` on the server.

---

## 5. Deploying to a VPS

Prerequisites:

- Ubuntu 24.04 LTS VPS with SSH access
- a domain name pointing to the VPS IP

Update `provisioning/secrets.env` with production values:

```
DASHBOARD_HOST=<VPS IP>
DASHBOARD_USER=<SSH user on VPS>
DOMAIN_NAME=yourdomain.example.com
SSL_EMAIL=you@example.com
DB_PASSWORD=<strong password>
GITHUB_TOKEN=<your token>
OPENROUTER_API_KEY=<your key>
```

Then deploy from your local machine:

```fish
make deploy
```

This is identical to `make deploy-vm` but does not skip the SSL tasks, so certbot
will obtain a certificate for the domain.

---

## 6. Migrating Bootstrapped Data to the VPS

After provisioning the VPS, import data from the dev VM so production starts with
a fully-evaluated dataset.

```fish
# Dump from the dev VM and copy to VPS
lxc exec $VM_NAME -- sudo -u postgres pg_dump craft_dashboard | gzip > craft-dashboard-initial.sql.gz
scp craft-dashboard-initial.sql.gz $DASHBOARD_USER@$DASHBOARD_HOST:~

# Restore on VPS
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl stop craft-dashboard"
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "gunzip -c craft-dashboard-initial.sql.gz | sudo -u postgres psql craft_dashboard"
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl start craft-dashboard"
```

Load `$DASHBOARD_USER` and `$DASHBOARD_HOST` from secrets.env first:

```fish
for line in (grep -v '^#' provisioning/secrets.env | grep '=')
    set -x (string split -m1 = $line)[1] (string split -m1 = $line)[2]
end
```

---

## 7. Ongoing Operations

Load variables from `secrets.env`, then SSH into the server.

```fish
# Load deployment vars into fish
for line in (grep -v '^#' provisioning/secrets.env | grep '=')
    set -x (string split -m1 = $line)[1] (string split -m1 = $line)[2]
end

# Application logs
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "journalctl -u craft-dashboard -f"

# Data collection logs
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "journalctl -u collect-data -f"

# Trigger manual data collection
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl start collect-data"

# Trigger manual LLM evaluation
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl start run-llm"

# List scheduled timers
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "systemctl list-timers"

# Download latest database backup
scp $DASHBOARD_USER@$DASHBOARD_HOST:/opt/craft-dashboard/backups/craft-dashboard-(date +%Y%m%d).sql.gz ~/backups/
```
