# Deployment

craft-dashboard is deployed via Ansible from your local machine. You do not
check out the project on the server. Ansible SSHes in, clones the repo from
GitHub, installs dependencies, runs migrations, and restarts services.

There are two deployment targets: an LXD VM (for development) and a VPS (for
production). The only difference is that the VPS deployment includes SSL
certificate setup via certbot.

## Configuration

### secrets.env

`provisioning/secrets.env` holds all deployment secrets. It is gitignored.
Copy the example file and fill in your values:

```
cp provisioning/secrets.env.example provisioning/secrets.env
```

Minimum required values for a dev VM:

```
VM_NAME=craft-dashboard-dev
DASHBOARD_USER=ubuntu
DASHBOARD_HOST=<VM IP, filled in after VM creation>
DOMAIN_NAME=localhost
DB_PASSWORD=dev-password-123
GITHUB_TOKEN=<your GitHub fine-grained token>
```

For production, also set:

```
DOMAIN_NAME=yourdomain.example.com
SSL_EMAIL=you@example.com
OPENROUTER_API_KEY=<your key>
ADMIN_TOKEN=<a random string for the admin API>
```

Ansible reads these values and writes them into `/opt/craft-dashboard/.env` on
the server. That server file is auto-generated; do not edit it by hand. To
change a setting on the server, edit `secrets.env` and re-deploy.

### .env (local development only)

`.env` in the repo root is for running the app or scripts directly on your
machine with `make dev` or `make collect`. It is read by pydantic-settings at
startup. If you only use the VM workflow, you do not need this file.

Some keys (`GITHUB_TOKEN`, `OPENROUTER_API_KEY`, `ADMIN_TOKEN`) appear in both
files. This is intentional: `secrets.env` feeds the server through Ansible,
while `.env` feeds your local process through pydantic.

### Which branch gets deployed

The repo and branch cloned on the server are set in
`provisioning/group_vars/all.yml`:

```yaml
app_repo:   https://github.com/mr-cal/craft-dashboard.git
app_branch: main
```

To ship a code change, push to the configured branch, then re-deploy.

## LXD VM setup (development)

### Create a VM

LXD containers do not support systemd reliably, so the `--vm` flag is required.

```fish
lxc launch ubuntu:24.04 craft-dashboard-dev --vm
sleep 30 && lxc exec craft-dashboard-dev -- cloud-init status --wait
```

### Get the VM IP

The VM may have multiple network interfaces. Parse the IP:

```fish
set VM_NAME (grep '^VM_NAME=' provisioning/secrets.env | cut -d= -f2)
set VM_USER (grep '^DASHBOARD_USER=' provisioning/secrets.env | cut -d= -f2)
set VM_IP (lxc list $VM_NAME -c4 --format csv | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo "VM: $VM_NAME  user: $VM_USER  IP: $VM_IP"
```

Update `DASHBOARD_HOST` in `provisioning/secrets.env` with this IP. Re-run this
block whenever the VM restarts, as LXD may assign a different IP.

### Set up SSH access

Ansible connects over SSH:

```fish
lxc exec $VM_NAME -- mkdir -p /home/$VM_USER/.ssh
lxc file push ~/.ssh/id_ed25519.pub $VM_NAME/home/$VM_USER/.ssh/authorized_keys
lxc exec $VM_NAME -- chown -R $VM_USER:$VM_USER /home/$VM_USER/.ssh
lxc exec $VM_NAME -- chmod 600 /home/$VM_USER/.ssh/authorized_keys
ssh -o StrictHostKeyChecking=no -l $VM_USER $VM_IP "echo SSH works"
```

If you don't have an SSH key yet:

```fish
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
```

### Deploy to the VM

```fish
make deploy-vm
```

This runs Ansible, which installs PostgreSQL, nginx, the app, and systemd
timers. It also runs database migrations. The `--skip-tags ssl` flag is
passed automatically so certbot is not invoked.

After provisioning, the dashboard is at `http://<VM_IP>/` (port 80, plain HTTP).
Do not use `https://`, port 8000, or `localhost`.

## VPS deployment (production)

Prerequisites:

- Ubuntu 24.04 LTS VPS with SSH access
- A domain name pointing to the VPS IP

Fill in `provisioning/secrets.env` with production values and deploy:

```fish
make deploy
```

Same as `make deploy-vm` but includes SSL (certbot obtains a certificate for
the configured domain).

Both `make deploy` and `make deploy-vm` are idempotent. Run them again to
apply changes.

## Migrating data from dev VM to production

After provisioning the VPS, you can import the dev VM's data so production
starts with a fully evaluated dataset.

```fish
# Dump from the dev VM
lxc exec $VM_NAME -- sudo -u postgres pg_dump craft_dashboard | gzip > craft-dashboard-initial.sql.gz

# Copy to VPS and restore
scp craft-dashboard-initial.sql.gz $DASHBOARD_USER@$DASHBOARD_HOST:~
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl stop craft-dashboard"
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "gunzip -c craft-dashboard-initial.sql.gz | sudo -u postgres psql craft_dashboard"
ssh -l $DASHBOARD_USER $DASHBOARD_HOST "sudo systemctl restart craft-dashboard"
```

Load the variables from secrets.env first:

```fish
for line in (grep -v '^#' provisioning/secrets.env | grep '=')
    set -x (string split -m1 = $line)[1] (string split -m1 = $line)[2]
end
```

## Ansible details

Ansible needs two Galaxy collections, installed automatically by `make deploy`
and `make deploy-vm`. To install them manually:

```fish
ansible-galaxy collection install -r provisioning/requirements.yml
```

The Ansible playbook is at `provisioning/playbook.yml`. Roles are in
`provisioning/roles/`. Variables are in `provisioning/group_vars/all.yml`.
