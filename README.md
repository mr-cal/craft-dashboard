# craft-dashboard

Dashboard, insights, and issue triage for the \*craft applications and libraries.

## Overview

craft-dashboard provides:
- **Issue & PR triage dashboard** with LLM-powered scoring and action suggestions
- **Statistics & trends** for open issues, PRs, releases, and dependencies
- **Multi-source data** from GitHub and Launchpad

---

## 1. Local Prerequisites

You only need these on your local machine:

- **uv** — Python package manager
- **Ansible 2.16+** — provisions the LXD VM and VPS over SSH
- **git**

PostgreSQL runs inside the LXD VM. You do not need it locally.

---

## 2. Linting and Unit Tests

Unit tests use mocks and do not require a running database:

```fish
make test   # run unit tests
make lint   # run linter
```

---

## 3. Setting Up an LXD VM

All application work — running the app, database migrations, data collection,
LLM evaluation — happens inside an LXD VM. This mirrors the production VPS
environment exactly.

> **Why VMs, not containers?** LXD containers don't support systemd reliably.
> The `--vm` flag gives a full Ubuntu 24.04 VM with real systemd services and timers.

### 3a. Create a VM

If you don't have one already:

```fish
lxc launch ubuntu:24.04 craft-dashboard-dev --vm
lxc exec craft-dashboard-dev -- cloud-init status --wait
```

Alternatively, if you use [local-llm](https://github.com/mr-cal/local-llm):

```fish
uv run llm lxd create 1 --lxd-vm
```

> local-llm renames the VM's default user to match your host username.
> Standard `lxc launch` keeps the user as `ubuntu`.

### 3b. Configure secrets

```fish
cp provisioning/secrets.env.example provisioning/secrets.env
```

Edit `provisioning/secrets.env`. Set at minimum:

```
VM_NAME=craft-dashboard-dev         # LXD VM name (lxc commands only)
DASHBOARD_USER=ubuntu               # VM SSH user (your host username if using local-llm)
DOMAIN_NAME=localhost
DB_PASSWORD=dev-password-123
GITHUB_TOKEN=<your GitHub fine-grained token>
```

`provisioning/secrets.env` is gitignored — never committed.

### 3c. Load VM variables

The VM may have multiple network interfaces; CSV output quotes multi-IP fields,
so parse with grep. Load all three variables at once:

```fish
set VM_NAME (grep '^VM_NAME=' provisioning/secrets.env | cut -d= -f2)
set VM_USER (grep '^DASHBOARD_USER=' provisioning/secrets.env | cut -d= -f2)
set VM_IP (lxc list $VM_NAME -c4 --format csv | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "VM: $VM_NAME  user: $VM_USER  IP: $VM_IP"
```

Update `DASHBOARD_HOST` in `provisioning/secrets.env` with this IP. Re-run this
block whenever the VM restarts, as LXD may assign a new IP.

### 3d. Set up SSH access

Ansible provisions the VM by SSHing in and running commands remotely — the same
way it will later connect to the production VPS. Ansible passes the username as
`-o User=...` internally, so usernames containing `@` work without any workarounds.

```fish
lxc exec $VM_NAME -- mkdir -p /home/$VM_USER/.ssh
lxc file push ~/.ssh/id_ed25519.pub $VM_NAME/home/$VM_USER/.ssh/authorized_keys
lxc exec $VM_NAME -- chown -R $VM_USER:$VM_USER /home/$VM_USER/.ssh
lxc exec $VM_NAME -- chmod 600 /home/$VM_USER/.ssh/authorized_keys
ssh -o StrictHostKeyChecking=no -l $VM_USER $VM_IP "echo SSH works"
```

### 3e. Provision the VM

```fish
make deploy-vm
```

This installs PostgreSQL, the app, nginx, and systemd timers inside the VM.
It also runs **database migrations** — the process that creates or updates the
database schema (tables, columns, indexes) in PostgreSQL. Re-run any time after
code changes; it is idempotent.

---

## 4. Bootstrapping Data (First Time)

After provisioning, collect and evaluate all historical data inside the VM before
deploying to a production VPS. Using your local LLM server avoids OpenRouter costs
for this one-time full pass over all historical issues.

### Step 1: Collect data from GitHub and Launchpad

```fish
lxc exec $VM_NAME -- systemctl start collect-data
lxc exec $VM_NAME -- journalctl -u collect-data -f
```

This fetches issues, PRs, releases, and snapstore data. Re-run any time to update.

### Step 2: Run LLM evaluation

```fish
# Full pass over all issues — may take several hours:
lxc exec $VM_NAME -- sudo -u craft-dashboard /opt/craft-dashboard/.venv/bin/python /opt/craft-dashboard/scripts/run_llm.py
```

After migration, the daily cron (`run_llm.py --open-only`) only processes
newly-changed open issues, so you only pay OpenRouter for incremental updates.

---

## 5. Deploying to a VPS

### Prerequisites

- Ubuntu 24.04 LTS VPS with SSH access
- A domain name pointing to the VPS IP

### Configure secrets for VPS

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

### Deploy

```fish
make deploy
```

---

## 6. Migrating Bootstrapped Data to the VPS

After provisioning the VPS (step 5), import the data from the VM so production
starts with a fully-evaluated dataset.

```fish
# Dump from the dev VM and copy to VPS
lxc exec $VM_NAME -- sudo -u postgres pg_dump craft_dashboard | gzip > craft-dashboard-initial.sql.gz
scp craft-dashboard-initial.sql.gz $DASHBOARD_USER@$DASHBOARD_HOST:~

# Restore on VPS
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl stop craft-dashboard"
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "gunzip -c craft-dashboard-initial.sql.gz | sudo -u postgres psql craft_dashboard"
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl start craft-dashboard"
```

> Load `$DASHBOARD_USER` and `$DASHBOARD_HOST` from secrets.env first:
> ```fish
> for line in (grep -v '^#' provisioning/secrets.env | grep '=')
>     set -x (string split -m1 = $line)[1] (string split -m1 = $line)[2]
> end
> ```

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
