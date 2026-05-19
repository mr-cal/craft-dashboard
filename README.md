# craft-dashboard

Dashboard, insights, and issue triage for the \*craft applications and libraries.

## Overview

craft-dashboard provides:
- **Issue & PR triage dashboard** with LLM-powered scoring and action suggestions
- **Statistics & trends** for open issues, PRs, releases, and dependencies
- **Multi-source data** from GitHub and Launchpad

---

## 1. Local Development

### Prerequisites

- Python 3.12+
- PostgreSQL 16+
- uv

### Setup

```bash
# Install dependencies
uv sync --group dev

# Set up environment variables
cp .env.example .env
# Edit .env with your database URL and API tokens

# Run database migrations
uv run alembic upgrade head

# Start development server
uv run uvicorn craft_dashboard.app:create_app --factory --reload
```

```bash
make test   # run tests
make lint   # run linter
```

---

## 2. Bootstrapping (First-Time Data Collection)

Before deploying, collect and evaluate all historical data **locally**. This avoids
paying OpenRouter API costs for the initial full pass of thousands of issues.

All commands below run **on your local machine**.

### Step 1: Create and migrate a local database

```bash
createdb craft_dashboard_local
cp .env.example .env
# Edit .env:
#   DATABASE_URL=postgresql+asyncpg://localhost/craft_dashboard_local
#   GITHUB_TOKEN=<fine-grained token with read access to public repos>
#   LLM_BACKEND=local
#   LOCAL_LLM_URL=<your local LLM server URL, e.g. https://192.168.1.x:8443/v1>
#   LOCAL_LLM_API_KEY=<bearer token if your server requires one>
uv run alembic upgrade head
```

### Step 2: Collect data from GitHub and Launchpad

```bash
make collect   # fetches issues, PRs, releases, and snapstore data
```

This takes a few minutes. Re-run it any time to update.

### Step 3: Run LLM evaluation

```bash
uv run scripts/run_llm.py   # evaluates ALL issues (open and closed) — may take hours
```

`make llm` only evaluates open issues. Use the script directly for the first full pass.
After migrating to the VPS, the daily cron job incrementally processes only newly-changed
open issues, so you only pay OpenRouter for updates.

---

## 3. Testing with an LXD VM

Test the full Ansible deployment locally before touching production. An LXD **virtual
machine** (not a container) gives you a real Ubuntu 24.04 environment with working
systemd services and timers — identical to the production VPS.

### 3a. Create a VM

If you don't have a VM already:

```bash
# On your local machine:
lxc launch ubuntu:24.04 craft-dashboard-test --vm

# Wait for cloud-init (~30 seconds)
lxc exec craft-dashboard-test -- cloud-init status --wait
```

Alternatively, if you use [local-llm](https://github.com/mr-cal/local-llm) for LXD
management:

```bash
uv run llm lxd create 1 --lxd-vm
```

> local-llm renames the VM's default user to match your host username. The standard
> `lxc launch` approach keeps it as `ubuntu`.

### 3b. Get the VM's IP address and SSH user

```bash
# On your local machine:
VM_IP=$(lxc list craft-dashboard-test --format csv -c 4 | cut -d' ' -f1)
echo "VM IP: $VM_IP"
```

The VM user depends on how you created it:
- `lxc launch` (standard): user is `ubuntu`
- `local-llm --lxd-vm`: user is your host username (run `echo $USER` to confirm)

### 3c. Set up SSH access

Ansible connects to the VM over SSH to provision it — it runs all the install and
configuration commands remotely. You need to plant your public key in the VM first.

```bash
# On your local machine (replace 'ubuntu' with your VM user if different):
VM_USER=ubuntu

lxc exec craft-dashboard-test -- mkdir -p /home/$VM_USER/.ssh
lxc file push ~/.ssh/id_ed25519.pub \
  "craft-dashboard-test/home/$VM_USER/.ssh/authorized_keys"
lxc exec craft-dashboard-test -- chown -R $VM_USER:$VM_USER \
  /home/$VM_USER/.ssh
lxc exec craft-dashboard-test -- chmod 600 \
  /home/$VM_USER/.ssh/authorized_keys
```

Verify SSH works. If your username contains `@` (e.g. `user@domain`), use `-l`:

```bash
# Standard username:
ssh -o StrictHostKeyChecking=no $VM_USER@$VM_IP "echo SSH works"

# Username with '@' (e.g. user@domain):
ssh -o StrictHostKeyChecking=no -l "$VM_USER" $VM_IP "echo SSH works"
```

### 3d. Configure secrets

```bash
# On your local machine:
cp provisioning/secrets.env.example provisioning/secrets.env
```

Edit `provisioning/secrets.env` and set at minimum:

```
DASHBOARD_HOST=<VM_IP from step 3b>
DASHBOARD_USER=<VM user from step 3b>
DOMAIN_NAME=localhost
DB_PASSWORD=test-password-123
GITHUB_TOKEN=<your token>
```

`provisioning/secrets.env` is gitignored and never committed.

### 3e. Deploy

```bash
# On your local machine — Ansible connects to the VM over SSH and provisions it:
make deploy-vm
```

This is idempotent. Re-run it any time after code or config changes.

### 3f. Verify

```bash
# On your local machine:
curl http://$VM_IP:8000/health
echo "Dashboard: http://$VM_IP:8000"

# Inspect services inside the VM (via lxc exec — no SSH needed for these):
lxc exec craft-dashboard-test -- systemctl status craft-dashboard
lxc exec craft-dashboard-test -- systemctl list-timers
lxc exec craft-dashboard-test -- journalctl -u craft-dashboard --no-pager -n 20
lxc exec craft-dashboard-test -- sudo -u postgres psql -c "\l" | grep craft_dashboard
```

### 3g. Port forwarding (optional)

If you prefer accessing via localhost instead of the VM IP:

```bash
# On your local machine:
lxc config device add craft-dashboard-test dashboard proxy \
  listen=tcp:0.0.0.0:8080 connect=tcp:127.0.0.1:8000
# Open http://localhost:8080
```

### 3h. Tear down

```bash
lxc delete craft-dashboard-test --force
```

---

## 4. Deploying to a VPS

### Prerequisites

- Ubuntu 24.04 LTS VPS with SSH access
- Ansible 2.16+ installed locally
- A domain name pointing to the VPS IP

### Configure secrets

```bash
cp provisioning/secrets.env.example provisioning/secrets.env
# Edit provisioning/secrets.env with your VPS IP, SSH user, passwords, and tokens
```

### Deploy

```bash
# On your local machine — Ansible SSHes into the VPS and provisions it:
make deploy
```

This is idempotent. Re-run it to update after code changes:
- Pulls the latest code from the main branch
- Installs new dependencies and runs migrations
- Restarts the application and reloads Nginx

---

## 5. Migrating Bootstrapped Data to the VPS

After provisioning completes (step 4), import the data you collected locally (step 2)
so the VPS starts with a fully-evaluated dataset.

All commands below run **on your local machine**.

```bash
# Dump the local database
pg_dump craft_dashboard_local | gzip > craft-dashboard-initial.sql.gz

# Copy to VPS
scp craft-dashboard-initial.sql.gz $DASHBOARD_USER@$DASHBOARD_HOST:~

# Restore on VPS
ssh $DASHBOARD_USER@$DASHBOARD_HOST "sudo systemctl stop craft-dashboard"
ssh $DASHBOARD_USER@$DASHBOARD_HOST \
  "gunzip -c craft-dashboard-initial.sql.gz | sudo -u postgres psql craft_dashboard"
ssh $DASHBOARD_USER@$DASHBOARD_HOST "sudo systemctl start craft-dashboard"
```

> If `$DASHBOARD_USER` contains `@`, use `ssh -l "$DASHBOARD_USER" $DASHBOARD_HOST` instead.

The VPS now has the full evaluated dataset. The daily cron job (`run_llm.py --open-only`)
then only processes newly-changed open issues incrementally.

---

## 6. Ongoing Operations

All commands below run **on your local machine** (they SSH into the server).

```bash
# Application logs
ssh $DASHBOARD_USER@$DASHBOARD_HOST "journalctl -u craft-dashboard -f"

# Data collection logs
ssh $DASHBOARD_USER@$DASHBOARD_HOST "journalctl -u collect-data -f"

# LLM evaluation logs
ssh $DASHBOARD_USER@$DASHBOARD_HOST "journalctl -u run-llm -f"

# Trigger manual data collection
ssh $DASHBOARD_USER@$DASHBOARD_HOST "sudo systemctl start collect-data"

# Trigger manual LLM evaluation
ssh $DASHBOARD_USER@$DASHBOARD_HOST "sudo systemctl start run-llm"

# List scheduled timers
ssh $DASHBOARD_USER@$DASHBOARD_HOST "systemctl list-timers"

# Download latest database backup
scp $DASHBOARD_USER@$DASHBOARD_HOST:/opt/craft-dashboard/backups/craft-dashboard-$(date +%Y%m%d).sql.gz ~/backups/
```

> Set `DASHBOARD_USER` and `DASHBOARD_HOST` from your `provisioning/secrets.env`,
> or prefix commands with `source provisioning/secrets.env &&`.
