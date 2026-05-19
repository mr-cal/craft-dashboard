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

- **uv** — Python package manager (runs the app and scripts)
- **Ansible 2.16+** — provisions the LXD VM and VPS over SSH
- **git**

PostgreSQL runs inside the LXD VM. You do not need it locally.

---

## 2. Linting and Unit Tests

Unit tests use mocks and do not require a running database:

```bash
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

```bash
# On your local machine:
lxc launch ubuntu:24.04 craft-dashboard-dev --vm
lxc exec craft-dashboard-dev -- cloud-init status --wait
```

Alternatively, if you use [local-llm](https://github.com/mr-cal/local-llm):

```bash
uv run llm lxd create 1 --lxd-vm
```

> local-llm renames the VM's default user to match your host username.
> Standard `lxc launch` keeps the user as `ubuntu`.

### 3b. Get the VM's IP address and username

```bash
# On your local machine:
VM_IP=$(lxc list craft-dashboard-dev --format csv -c 4 | cut -d' ' -f1)
echo "IP: $VM_IP"

# The VM username:
#   - standard lxc launch: ubuntu
#   - local-llm --lxd-vm:  your host username (run: echo $USER)
VM_USER=ubuntu   # adjust if needed
```

### 3c. Set up SSH access

Ansible provisions the VM by SSHing into it and running commands remotely —
the same way it will later connect to the production VPS. Ansible passes the
username via `ssh -o User="..."`, so usernames containing `@` work without
any special handling.

```bash
# On your local machine:
lxc exec craft-dashboard-dev -- mkdir -p /home/$VM_USER/.ssh
lxc file push ~/.ssh/id_ed25519.pub \
  "craft-dashboard-dev/home/$VM_USER/.ssh/authorized_keys"
lxc exec craft-dashboard-dev -- \
  chown -R $VM_USER:$VM_USER /home/$VM_USER/.ssh
lxc exec craft-dashboard-dev -- \
  chmod 600 /home/$VM_USER/.ssh/authorized_keys

# Verify:
ssh -o StrictHostKeyChecking=no -l "$VM_USER" $VM_IP "echo SSH works"
```

### 3d. Configure secrets

```bash
# On your local machine:
cp provisioning/secrets.env.example provisioning/secrets.env
```

Edit `provisioning/secrets.env` and set at minimum:

```bash
DASHBOARD_HOST=<VM_IP from step 3b>
DASHBOARD_USER=<VM_USER from step 3b>
DOMAIN_NAME=localhost
DB_PASSWORD=dev-password-123
GITHUB_TOKEN=<your GitHub fine-grained token>
```

`provisioning/secrets.env` is gitignored — never committed.

### 3e. Provision the VM

```bash
# On your local machine — Ansible SSHes into the VM and sets everything up:
make deploy-vm
```

This installs PostgreSQL, the app, nginx, and systemd timers. It also runs
**database migrations** — the process that creates or updates the database
schema (tables, columns, indexes). You never need to run migrations by hand;
`make deploy-vm` does it on every run.

Re-run `make deploy-vm` any time after a code change to update the VM.

---

## 4. Bootstrapping Data (First Time)

After provisioning, collect and evaluate all historical data inside the VM before
deploying to a production VPS. Using your local LLM server avoids OpenRouter API
costs for this one-time full pass.

### Step 1: Collect data from GitHub and Launchpad

```bash
# Trigger from your local machine; runs inside the VM:
lxc exec craft-dashboard-dev -- systemctl start collect-data

# Watch progress:
lxc exec craft-dashboard-dev -- journalctl -u collect-data -f
```

This fetches issues, PRs, releases, and snapstore data. Re-run any time to update.

### Step 2: Run LLM evaluation

For the initial full pass of all issues (open and closed):

```bash
# Inside the VM (this may take several hours):
lxc exec craft-dashboard-dev -- \
  sudo -u craft-dashboard \
  /opt/craft-dashboard/.venv/bin/python \
  /opt/craft-dashboard/scripts/run_llm.py
```

After migration, the daily cron (`run_llm.py --open-only`) only processes
newly-changed open issues incrementally, so you only pay OpenRouter for updates.

---

## 5. Deploying to a VPS

### Prerequisites

- Ubuntu 24.04 LTS VPS with SSH access
- A domain name pointing to the VPS IP

### Configure secrets for VPS

Update `provisioning/secrets.env` with your VPS details (or keep separate files
for dev vs prod):

```bash
DASHBOARD_HOST=<VPS IP>
DASHBOARD_USER=<SSH user on VPS>
DOMAIN_NAME=yourdomain.example.com
SSL_EMAIL=you@example.com
DB_PASSWORD=<strong password>
GITHUB_TOKEN=<your token>
OPENROUTER_API_KEY=<your key>   # used for ongoing incremental evaluations
```

### Deploy

```bash
# On your local machine:
make deploy
```

---

## 6. Migrating Bootstrapped Data to the VPS

After provisioning the VPS (step 5), import the data you collected in the VM so
production starts with a fully-evaluated dataset.

```bash
# On your local machine:

# Dump data from the dev VM
lxc exec craft-dashboard-dev -- \
  sudo -u postgres pg_dump craft_dashboard | gzip > craft-dashboard-initial.sql.gz

# Copy to VPS and restore
scp craft-dashboard-initial.sql.gz $DASHBOARD_HOST:~

# (sourcing secrets.env gives you $DASHBOARD_USER and $DASHBOARD_HOST)
source provisioning/secrets.env
ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST "sudo systemctl stop craft-dashboard"
ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST \
  "gunzip -c craft-dashboard-initial.sql.gz | sudo -u postgres psql craft_dashboard"
ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST "sudo systemctl start craft-dashboard"
```

---

## 7. Ongoing Operations

Source `provisioning/secrets.env` to get `$DASHBOARD_USER` and `$DASHBOARD_HOST`,
then SSH into the server. All commands below run **on your local machine**.

```bash
source provisioning/secrets.env

# Application logs
ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST "journalctl -u craft-dashboard -f"

# Data collection logs
ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST "journalctl -u collect-data -f"

# Trigger manual data collection
ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST "sudo systemctl start collect-data"

# Trigger manual LLM evaluation
ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST "sudo systemctl start run-llm"

# List scheduled timers
ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST "systemctl list-timers"

# Download latest database backup
scp "$DASHBOARD_USER@$DASHBOARD_HOST:/opt/craft-dashboard/backups/craft-dashboard-$(date +%Y%m%d).sql.gz" ~/backups/
```
