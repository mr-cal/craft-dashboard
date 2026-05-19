# craft-dashboard

Dashboard, insights, and issue triage for the \*craft applications and libraries.

## Overview

craft-dashboard provides:
- **Issue & PR triage dashboard** with LLM-powered scoring and action suggestions
- **Statistics & trends** for open issues, PRs, releases, and dependencies
- **Multi-source data** from GitHub and Launchpad

## Development

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

### Testing

```bash
make test
```

### Linting

```bash
make lint
```

## Deployment

### Prerequisites

- A VPS running Ubuntu 24.04 LTS (or an LXD VM for testing — see below)
- SSH access to the target
- Ansible 2.16+ installed locally
- A domain name pointing to the VPS IP (not needed for LXD testing)

### Configure Deployment Secrets

Copy the example secrets file and fill in your values:

```bash
cp provisioning/secrets.env.example provisioning/secrets.env
# Edit provisioning/secrets.env with your passwords and tokens
```

`provisioning/secrets.env` is gitignored and never committed. Fill it in once;
all deployment commands source it automatically.

### Deploy to VPS

```bash
make deploy
```

### Deploy to LXD VM (testing)

```bash
make deploy-vm  # same as make deploy but skips SSL cert provisioning
```

### Re-deploying

Both targets are idempotent — run them again to update:

- Pull the latest code from the main branch
- Install any new dependencies
- Run database migrations
- Restart the application
- Reload Nginx

### Manual Operations

```bash
# Check application logs
ssh ubuntu@your-vps "journalctl -u craft-dashboard -f"

# Check data collection logs
ssh ubuntu@your-vps "journalctl -u collect-data -f"

# Check LLM evaluation logs
ssh ubuntu@your-vps "journalctl -u run-llm -f"

# Trigger manual data collection
ssh ubuntu@your-vps "sudo systemctl start collect-data"

# Trigger manual LLM evaluation
ssh ubuntu@your-vps "sudo systemctl start run-llm"

# Check timer status
ssh ubuntu@your-vps "systemctl list-timers"

# Copy latest backup to your local machine
scp ubuntu@your-vps:/opt/craft-dashboard/backups/craft-dashboard-$(date +%Y%m%d).sql.gz ~/backups/
```

## Bootstrapping (First-Time Data Collection)

On first use, collect and evaluate all historical data locally — this avoids
running the initial full pass through OpenRouter and incurring API costs.

### Step 1: Set up a local PostgreSQL database

```bash
createdb craft_dashboard_local
cp .env.example .env
# Set DATABASE_URL=postgresql+asyncpg://localhost/craft_dashboard_local
# Set GITHUB_TOKEN to a fine-grained token with read access to public repos
# Set LLM_BACKEND=local and LOCAL_LLM_URL/LOCAL_LLM_API_KEY for your server
uv run alembic upgrade head
```

### Step 2: Collect data from GitHub and Launchpad

```bash
make collect   # runs scripts/collect_data.py --source all
```

This fetches issues, PRs, releases, and snapstore data. Expect it to take a few
minutes. Re-run it any time to update.

### Step 3: Run LLM evaluation with your local server

```bash
make llm       # runs scripts/run_llm.py --open-only
```

For the first full pass (all issues, not just open ones), run directly:

```bash
uv run scripts/run_llm.py   # evaluates all issues — may take hours
```

Using a local LLM server (`LLM_BACKEND=local`) avoids OpenRouter costs for this
one-time full pass. After migration, the daily cron job only processes newly-changed
open issues incrementally.

### Step 4: Migrate the bootstrapped database to the VPS

After Ansible provisioning is complete:

```bash
# Dump the local database (includes data + LLM evaluations)
pg_dump craft_dashboard_local | gzip > craft-dashboard-initial.sql.gz

# Copy to VPS
scp craft-dashboard-initial.sql.gz ubuntu@your-vps:~

# Restore on VPS (stop the app first to avoid conflicts)
ssh ubuntu@your-vps "sudo systemctl stop craft-dashboard"
ssh ubuntu@your-vps "gunzip -c craft-dashboard-initial.sql.gz | sudo -u postgres psql craft_dashboard"
ssh ubuntu@your-vps "sudo systemctl start craft-dashboard"
```

The VPS now has the full evaluated dataset. The daily OpenRouter cron job
(`run_llm.py --open-only`) then only processes newly-changed open issues.

## Testing with an LXD VM

You can test the full deployment locally using an LXD virtual machine. This gives
you a real Ubuntu 24.04 environment identical to the production VPS.

> **Note:** Use `--vm` (not a plain container) to get full systemd support.
> Containers don't support systemd services and timers reliably.

### Creating a VM

If you don't have a VM already, create one:

```bash
# Launch an Ubuntu 24.04 VM
lxc launch ubuntu:24.04 craft-dashboard-test --vm

# Wait for cloud-init to finish (~30 seconds)
lxc exec craft-dashboard-test -- cloud-init status --wait

# Get the VM's IP address
VM_IP=$(lxc list craft-dashboard-test --format csv -c 4 | cut -d' ' -f1)
echo "VM IP: $VM_IP"
```

Alternatively, if you use the [local-llm](https://github.com/mr-cal/local-llm) repo
for LXD management, you can create a VM with:

```bash
uv run llm lxd create 1 --lxd-vm
```

Note: local-llm VMs rename the default user to your host username. Set
`DASHBOARD_USER` in `provisioning/secrets.env` to match (e.g. `DASHBOARD_USER=yourname`).

### Set Up SSH Access

```bash
# Copy your SSH public key into the VM
lxc exec craft-dashboard-test -- mkdir -p /home/ubuntu/.ssh
lxc file push ~/.ssh/id_ed25519.pub craft-dashboard-test/home/ubuntu/.ssh/authorized_keys
lxc exec craft-dashboard-test -- chown -R ubuntu:ubuntu /home/ubuntu/.ssh
lxc exec craft-dashboard-test -- chmod 600 /home/ubuntu/.ssh/authorized_keys

# Verify SSH works
ssh -o StrictHostKeyChecking=no ubuntu@$VM_IP "echo 'SSH works!'"
```

### Deploy to the VM

Edit `provisioning/secrets.env` and set `DASHBOARD_HOST=$VM_IP` and
`DOMAIN_NAME=localhost`, then:

```bash
make deploy-vm
```

### Verify the Deployment

```bash
# Health check
curl http://$VM_IP:8000/health

# Open the dashboard in your browser
echo "Dashboard: http://$VM_IP:8000"

# Check services inside the VM
lxc exec craft-dashboard-test -- systemctl status craft-dashboard
lxc exec craft-dashboard-test -- systemctl list-timers
lxc exec craft-dashboard-test -- journalctl -u craft-dashboard --no-pager -n 20

# Check PostgreSQL
lxc exec craft-dashboard-test -- sudo -u postgres psql -c "\l" | grep craft_dashboard
```

### Iterate on Changes

```bash
# Re-run the playbook after code changes (idempotent)
make deploy-vm

# Or just restart the app inside the VM
lxc exec craft-dashboard-test -- systemctl restart craft-dashboard
```

### Port Forwarding (optional)

Access the VM's port 8000 on localhost:8080:

```bash
lxc config device add craft-dashboard-test dashboard proxy \
  listen=tcp:0.0.0.0:8080 connect=tcp:127.0.0.1:8000
# Then open http://localhost:8080
```

### Tear Down

```bash
lxc delete craft-dashboard-test --force
```
