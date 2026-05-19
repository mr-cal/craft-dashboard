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

\`\`\`bash
# Install dependencies
uv sync --group dev

# Set up environment variables
cp .env.example .env
# Edit .env with your database URL and API tokens

# Run database migrations
uv run alembic upgrade head

# Start development server
uv run uvicorn craft_dashboard.app:create_app --factory --reload
\`\`\`

### Testing

\`\`\`bash
make test
\`\`\`

### Linting

\`\`\`bash
make lint
\`\`\`

## Deployment

### Prerequisites

- A VPS running Ubuntu 24.04 LTS (or an LXD VM for testing — see below)
- SSH access to the target
- Ansible 2.16+ installed locally
- A domain name pointing to the VPS IP (not needed for LXD testing)

### Environment Variables

Create a file with your deployment secrets and source it before running Ansible:

\`\`\`bash
export DASHBOARD_HOST=your-vps-ip
export DASHBOARD_USER=ubuntu
export DASHBOARD_SSH_KEY=~/.ssh/id_ed25519
export DB_PASSWORD=your-secure-database-password
export GITHUB_TOKEN=your-github-token
export OPENROUTER_API_KEY=your-openrouter-key  # Optional
export ADMIN_TOKEN=your-admin-token             # Optional
export DOMAIN_NAME=dashboard.example.com
export SSL_EMAIL=admin@example.com
\`\`\`

### Running the Playbook

\`\`\`bash
cd provisioning
ansible-playbook playbook.yml
\`\`\`

### Re-deploying

The playbook is idempotent. Run it again to update:

\`\`\`bash
cd provisioning
ansible-playbook playbook.yml
\`\`\`

This will:
- Pull the latest code from the main branch
- Install any new dependencies
- Run database migrations
- Restart the application
- Reload Nginx

### Manual Operations

\`\`\`bash
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

# Or copy all backups
scp -r ubuntu@your-vps:/opt/craft-dashboard/backups/ ~/backups/craft-dashboard/

# Restore a backup locally (for testing)
gunzip -c ~/backups/craft-dashboard-20260101.sql.gz | psql craft_dashboard
\`\`\`

### Initial LLM Pass with Local LLM Server (Recommended)

Before deploying to the VPS, run a full LLM evaluation using your local LLM server to
avoid spending money on OpenRouter for the first pass of all historical issues.

**Step 1: Collect data locally (into your local PostgreSQL)**

\`\`\`bash
export DATABASE_URL=postgresql+asyncpg://localhost/craft_dashboard_local
export GITHUB_TOKEN=your-github-token
uv run scripts/collect_data.py --source all
\`\`\`

**Step 2: Run the full LLM evaluation against your local server**

\`\`\`bash
export LLM_BACKEND=local
export LOCAL_LLM_URL=http://192.168.1.x:port/v1   # your server's address
export LOCAL_LLM_SUMMARY_MODEL=your-model-name
export LOCAL_LLM_EVALUATION_MODEL=your-model-name

uv run scripts/run_llm.py  # evaluates all issues (open and closed)
\`\`\`

This may take several hours depending on the number of issues and model speed.

**Step 3: Export and import to the VPS**

\`\`\`bash
# Dump the local database (includes collected data + LLM evaluations)
pg_dump craft_dashboard_local | gzip > craft-dashboard-initial.sql.gz

# Copy to VPS
scp craft-dashboard-initial.sql.gz ubuntu@your-vps:~

# Restore on VPS (before running the Ansible playbook, or after with app stopped)
ssh ubuntu@your-vps "gunzip -c craft-dashboard-initial.sql.gz | sudo -u postgres psql craft_dashboard"
\`\`\`

After this, the VPS has a fully-evaluated dataset. The daily OpenRouter cron job
(\`run_llm.py --open-only\`) then only processes newly-changed open issues incrementally.

## Testing with LXD VM

You can test the full deployment locally using an LXD virtual machine. This gives
you a real Ubuntu 24.04 environment identical to the production VPS.

### Prerequisites

Install LXD if not already available:

\`\`\`bash
sudo snap install lxd
lxd init --auto  # Accept defaults for local testing
\`\`\`

### Launch a Test VM

\`\`\`bash
# Launch an Ubuntu 24.04 VM (not a container — VM gives full systemd support)
lxc launch ubuntu:24.04 craft-dashboard-test --vm

# Wait for the VM to finish cloud-init (~30 seconds)
lxc exec craft-dashboard-test -- cloud-init status --wait

# Get the VM's IP address
VM_IP=$(lxc list craft-dashboard-test --format csv -c 4 | cut -d' ' -f1)
echo "VM IP: $VM_IP"
\`\`\`

### Set Up SSH Access

\`\`\`bash
# Copy your SSH public key into the VM
lxc exec craft-dashboard-test -- mkdir -p /home/ubuntu/.ssh
lxc file push ~/.ssh/id_ed25519.pub craft-dashboard-test/home/ubuntu/.ssh/authorized_keys
lxc exec craft-dashboard-test -- chown -R ubuntu:ubuntu /home/ubuntu/.ssh
lxc exec craft-dashboard-test -- chmod 600 /home/ubuntu/.ssh/authorized_keys

# Verify SSH works
ssh -o StrictHostKeyChecking=no ubuntu@$VM_IP "echo 'SSH works!'"
\`\`\`

### Deploy to the LXD VM

\`\`\`bash
# Set environment variables for LXD deployment
export DASHBOARD_HOST=$VM_IP
export DASHBOARD_USER=ubuntu
export DB_PASSWORD=test-password-123
export GITHUB_TOKEN=your-github-token
export DOMAIN_NAME=localhost  # No real domain needed for testing

# Run the playbook (skip SSL since we don't have a real domain)
cd provisioning
ansible-playbook playbook.yml --skip-tags ssl
\`\`\`

### Verify the Deployment

\`\`\`bash
# Health check
curl http://$VM_IP:8000/health

# Open the dashboard in your browser
echo "Dashboard: http://$VM_IP:8000"

# Check services inside the VM
lxc exec craft-dashboard-test -- systemctl status craft-dashboard
lxc exec craft-dashboard-test -- systemctl list-timers
lxc exec craft-dashboard-test -- journalctl -u craft-dashboard --no-pager -n 20

# Check PostgreSQL
lxc exec craft-dashboard-test -- sudo -u postgres psql -c "\\l" | grep craft_dashboard
\`\`\`

### Iterate on Changes

\`\`\`bash
# Re-run the playbook after code changes (idempotent)
cd provisioning
ansible-playbook playbook.yml --skip-tags ssl

# Or just restart the app after local changes pushed to the repo
lxc exec craft-dashboard-test -- systemctl restart craft-dashboard
\`\`\`

### Tear Down

\`\`\`bash
# Delete the VM when done
lxc delete craft-dashboard-test --force
\`\`\`

### Tips

- Use \`--vm\` flag (not plain \`lxc launch\`) to get full systemd support. Containers
  don't support systemd services and timers reliably.
- Skip the \`ssl\` tag for local testing since certbot needs a real domain.
- The VM is ephemeral — re-create it any time for a clean test.
- Forward a port if you prefer accessing via localhost:
  \`\`\`bash
  lxc config device add craft-dashboard-test dashboard proxy \
    listen=tcp:0.0.0.0:8080 connect=tcp:127.0.0.1:8000
  # Then access http://localhost:8080
  \`\`\`
