# Plan 6: Provisioning & Deployment

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create Ansible playbooks to provision a VPS with PostgreSQL, Nginx, systemd services, cron jobs, SSL certificates, and unattended upgrades. The provisioning is idempotent — re-running updates the deployment without data loss.

**Architecture:** Ansible roles handle each concern: `common` (base packages, security), `postgresql` (database setup), `app` (application deployment with gunicorn + systemd), `nginx` (reverse proxy with Let's Encrypt SSL), and `cron` (systemd timers for data collection and LLM evaluation). Secrets are read from a `.env` file on the server.

**Tech Stack:** Ansible 2.16+, Ubuntu 24.04 LTS, Nginx, systemd, Let's Encrypt (certbot), PostgreSQL 16

**Depends on:** Plans 1–5

---

### Task 1: Ansible Project Structure and Inventory

**Files:**
- Create: `provisioning/inventory.yml`
- Create: `provisioning/ansible.cfg`
- Create: `provisioning/group_vars/all.yml`

- [ ] **Step 1: Create `provisioning/ansible.cfg`**

```ini
[defaults]
inventory = inventory.yml
roles_path = roles
host_key_checking = False
retry_files_enabled = False

[privilege_escalation]
become = True
become_method = sudo
```

- [ ] **Step 2: Create `provisioning/inventory.yml`**

```yaml
all:
  hosts:
    dashboard:
      ansible_host: "{{ lookup('env', 'DASHBOARD_HOST') }}"
      ansible_user: "{{ lookup('env', 'DASHBOARD_USER') | default('ubuntu', true) }}"
      ansible_ssh_private_key_file: "{{ lookup('env', 'DASHBOARD_SSH_KEY') | default('~/.ssh/id_ed25519', true) }}"
```

- [ ] **Step 3: Create `provisioning/group_vars/all.yml`**

```yaml
# Application settings
app_name: craft-dashboard
app_user: craft-dashboard
app_group: craft-dashboard
app_dir: /opt/craft-dashboard
app_venv: /opt/craft-dashboard/.venv
app_repo: https://github.com/mr-cal/craft-dashboard.git
app_branch: main

# Python
python_version: "3.12"

# PostgreSQL
db_name: craft_dashboard
db_user: craft_dashboard
db_password: "{{ lookup('env', 'DB_PASSWORD') }}"

# Networking
domain_name: "{{ lookup('env', 'DOMAIN_NAME') | default('dashboard.example.com', true) }}"
app_port: 8000
nginx_ssl_email: "{{ lookup('env', 'SSL_EMAIL') | default('admin@example.com', true) }}"

# Environment variables for the application
app_env:
  DATABASE_URL: "postgresql+asyncpg://{{ db_user }}:{{ db_password }}@localhost/{{ db_name }}"
  GITHUB_TOKEN: "{{ lookup('env', 'GITHUB_TOKEN') }}"
  OPENROUTER_API_KEY: "{{ lookup('env', 'OPENROUTER_API_KEY') }}"
  ADMIN_TOKEN: "{{ lookup('env', 'ADMIN_TOKEN') }}"
  DEBUG: "false"

# Cron schedules (systemd timer OnCalendar format)
collect_schedule: "*-*-* 02:00:00"    # Daily at 2 AM UTC
llm_schedule: "*-*-* 06:00:00"       # Daily at 6 AM UTC (4 hours after collection)
```

- [ ] **Step 4: Commit**

```bash
git add provisioning/ansible.cfg provisioning/inventory.yml provisioning/group_vars/
git commit -m "feat: add Ansible project structure and inventory"
```

---

### Task 2: Common Role — Base Packages and Security

**Files:**
- Create: `provisioning/roles/common/tasks/main.yml`
- Create: `provisioning/roles/common/handlers/main.yml`

- [ ] **Step 1: Create `provisioning/roles/common/tasks/main.yml`**

```yaml
---
- name: Update apt cache
  ansible.builtin.apt:
    update_cache: true
    cache_valid_time: 3600

- name: Install base packages
  ansible.builtin.apt:
    name:
      - build-essential
      - curl
      - git
      - htop
      - python3
      - python3-pip
      - python3-venv
      - python3-dev
      - libpq-dev
      - unattended-upgrades
      - apt-listchanges
      - ufw
    state: present

- name: Enable unattended upgrades
  ansible.builtin.copy:
    dest: /etc/apt/apt.conf.d/20auto-upgrades
    content: |
      APT::Periodic::Update-Package-Lists "1";
      APT::Periodic::Unattended-Upgrade "1";
      APT::Periodic::AutocleanInterval "7";
    mode: "0644"

- name: Configure unattended upgrades — security only
  ansible.builtin.copy:
    dest: /etc/apt/apt.conf.d/50unattended-upgrades
    content: |
      Unattended-Upgrade::Allowed-Origins {
          "${distro_id}:${distro_codename}-security";
          "${distro_id}ESMApps:${distro_codename}-apps-security";
          "${distro_id}ESM:${distro_codename}-infra-security";
      };
      Unattended-Upgrade::AutoFixInterruptedDpkg "true";
      Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
      Unattended-Upgrade::Remove-Unused-Dependencies "true";
      Unattended-Upgrade::Automatic-Reboot "false";
    mode: "0644"

- name: Configure UFW — allow SSH
  community.general.ufw:
    rule: allow
    name: OpenSSH
    state: enabled

- name: Configure UFW — allow HTTP
  community.general.ufw:
    rule: allow
    port: "80"
    proto: tcp

- name: Configure UFW — allow HTTPS
  community.general.ufw:
    rule: allow
    port: "443"
    proto: tcp

- name: Enable UFW
  community.general.ufw:
    state: enabled
    policy: deny
    direction: incoming

- name: Create application user
  ansible.builtin.user:
    name: "{{ app_user }}"
    system: true
    shell: /usr/sbin/nologin
    home: "{{ app_dir }}"
    create_home: false

- name: Create application directory
  ansible.builtin.file:
    path: "{{ app_dir }}"
    state: directory
    owner: "{{ app_user }}"
    group: "{{ app_group }}"
    mode: "0755"
```

- [ ] **Step 2: Create `provisioning/roles/common/handlers/main.yml`**

```yaml
---
# No handlers needed for common role currently
```

- [ ] **Step 3: Commit**

```bash
git add provisioning/roles/common/
git commit -m "feat: add common Ansible role with base packages and security"
```

---

### Task 3: PostgreSQL Role

**Files:**
- Create: `provisioning/roles/postgresql/tasks/main.yml`
- Create: `provisioning/roles/postgresql/handlers/main.yml`

- [ ] **Step 1: Create `provisioning/roles/postgresql/tasks/main.yml`**

```yaml
---
- name: Install PostgreSQL 16
  ansible.builtin.apt:
    name:
      - postgresql-16
      - postgresql-client-16
      - python3-psycopg2
    state: present

- name: Ensure PostgreSQL is running
  ansible.builtin.systemd:
    name: postgresql
    state: started
    enabled: true

- name: Create database user
  become: true
  become_user: postgres
  community.postgresql.postgresql_user:
    name: "{{ db_user }}"
    password: "{{ db_password }}"
    role_attr_flags: CREATEDB

- name: Create database
  become: true
  become_user: postgres
  community.postgresql.postgresql_db:
    name: "{{ db_name }}"
    owner: "{{ db_user }}"
    encoding: UTF-8

- name: Allow local password authentication for app user
  ansible.builtin.lineinfile:
    path: /etc/postgresql/16/main/pg_hba.conf
    insertbefore: "^local\\s+all\\s+all"
    line: "local   {{ db_name }}    {{ db_user }}    scram-sha-256"
    state: present
  notify: Restart PostgreSQL
```

- [ ] **Step 2: Create `provisioning/roles/postgresql/handlers/main.yml`**

```yaml
---
- name: Restart PostgreSQL
  ansible.builtin.systemd:
    name: postgresql
    state: restarted
```

- [ ] **Step 3: Commit**

```bash
git add provisioning/roles/postgresql/
git commit -m "feat: add PostgreSQL Ansible role"
```

---

### Task 4: App Deployment Role

**Files:**
- Create: `provisioning/roles/app/tasks/main.yml`
- Create: `provisioning/roles/app/templates/env.j2`
- Create: `provisioning/roles/app/templates/craft-dashboard.service.j2`
- Create: `provisioning/roles/app/handlers/main.yml`

- [ ] **Step 1: Create the systemd service template**

Create `provisioning/roles/app/templates/craft-dashboard.service.j2`:
```ini
[Unit]
Description=craft-dashboard web application
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=exec
User={{ app_user }}
Group={{ app_group }}
WorkingDirectory={{ app_dir }}
EnvironmentFile={{ app_dir }}/.env
ExecStart={{ app_venv }}/bin/gunicorn \
    --bind 127.0.0.1:{{ app_port }} \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --access-logfile - \
    --error-logfile - \
    "craft_dashboard.app:create_app()"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the .env template**

Create `provisioning/roles/app/templates/env.j2`:
```bash
# Auto-generated by Ansible — do not edit manually
{% for key, value in app_env.items() %}
{{ key }}={{ value }}
{% endfor %}
```

- [ ] **Step 3: Create `provisioning/roles/app/tasks/main.yml`**

```yaml
---
- name: Install uv
  ansible.builtin.shell: |
    curl -LsSf https://astral.sh/uv/install.sh | sh
  args:
    creates: /root/.local/bin/uv

- name: Clone or update application repo
  ansible.builtin.git:
    repo: "{{ app_repo }}"
    dest: "{{ app_dir }}"
    version: "{{ app_branch }}"
    force: true
  become: true
  become_user: "{{ app_user }}"
  notify: Restart craft-dashboard

- name: Create virtual environment and install dependencies
  ansible.builtin.shell: |
    /root/.local/bin/uv venv {{ app_venv }} --python {{ python_version }}
    /root/.local/bin/uv pip install --python {{ app_venv }}/bin/python -e "{{ app_dir }}"
  args:
    chdir: "{{ app_dir }}"
  notify: Restart craft-dashboard

- name: Deploy environment file
  ansible.builtin.template:
    src: env.j2
    dest: "{{ app_dir }}/.env"
    owner: "{{ app_user }}"
    group: "{{ app_group }}"
    mode: "0600"
  notify: Restart craft-dashboard

- name: Install psycopg2 for sync Alembic migrations
  ansible.builtin.shell: |
    /root/.local/bin/uv pip install --python {{ app_venv }}/bin/python psycopg2-binary
  args:
    chdir: "{{ app_dir }}"

- name: Run database migrations
  ansible.builtin.shell: |
    {{ app_venv }}/bin/alembic upgrade head
  args:
    chdir: "{{ app_dir }}"
  environment: "{{ app_env }}"
  become: true
  become_user: "{{ app_user }}"

- name: Deploy systemd service
  ansible.builtin.template:
    src: craft-dashboard.service.j2
    dest: /etc/systemd/system/craft-dashboard.service
    mode: "0644"
  notify:
    - Reload systemd
    - Restart craft-dashboard

- name: Enable and start craft-dashboard
  ansible.builtin.systemd:
    name: craft-dashboard
    state: started
    enabled: true
```

- [ ] **Step 4: Create `provisioning/roles/app/handlers/main.yml`**

```yaml
---
- name: Reload systemd
  ansible.builtin.systemd:
    daemon_reload: true

- name: Restart craft-dashboard
  ansible.builtin.systemd:
    name: craft-dashboard
    state: restarted
```

- [ ] **Step 5: Commit**

```bash
git add provisioning/roles/app/
git commit -m "feat: add app deployment Ansible role with systemd service"
```

---

### Task 5: Nginx Role with SSL

**Files:**
- Create: `provisioning/roles/nginx/tasks/main.yml`
- Create: `provisioning/roles/nginx/templates/craft-dashboard.conf.j2`
- Create: `provisioning/roles/nginx/handlers/main.yml`

- [ ] **Step 1: Create the Nginx config template**

Create `provisioning/roles/nginx/templates/craft-dashboard.conf.j2`:
```nginx
server {
    listen 80;
    server_name {{ domain_name }};

    # Redirect HTTP to HTTPS (after certbot sets up SSL)
    location / {
        return 301 https://$host$request_uri;
    }

    # Let's Encrypt challenge
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }
}

server {
    listen 443 ssl http2;
    server_name {{ domain_name }};

    ssl_certificate /etc/letsencrypt/live/{{ domain_name }}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{{ domain_name }}/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=63072000" always;

    location / {
        proxy_pass http://127.0.0.1:{{ app_port }};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias {{ app_dir }}/craft_dashboard/static/;
        expires 1d;
        add_header Cache-Control "public, no-transform";
    }
}
```

- [ ] **Step 2: Create `provisioning/roles/nginx/tasks/main.yml`**

```yaml
---
- name: Install Nginx and Certbot
  ansible.builtin.apt:
    name:
      - nginx
      - certbot
      - python3-certbot-nginx
    state: present

- name: Remove default Nginx site
  ansible.builtin.file:
    path: /etc/nginx/sites-enabled/default
    state: absent
  notify: Reload Nginx

- name: Deploy Nginx config (HTTP only initially for certbot)
  ansible.builtin.copy:
    dest: /etc/nginx/sites-available/craft-dashboard
    content: |
      server {
          listen 80;
          server_name {{ domain_name }};

          location / {
              proxy_pass http://127.0.0.1:{{ app_port }};
              proxy_set_header Host $host;
              proxy_set_header X-Real-IP $remote_addr;
              proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
              proxy_set_header X-Forwarded-Proto $scheme;
          }

          location /.well-known/acme-challenge/ {
              root /var/www/html;
          }
      }
    mode: "0644"
  notify: Reload Nginx

- name: Enable Nginx site
  ansible.builtin.file:
    src: /etc/nginx/sites-available/craft-dashboard
    dest: /etc/nginx/sites-enabled/craft-dashboard
    state: link
  notify: Reload Nginx

- name: Ensure Nginx is running
  ansible.builtin.systemd:
    name: nginx
    state: started
    enabled: true

- name: Obtain SSL certificate (if not already present)
  ansible.builtin.shell: |
    certbot certonly --nginx \
      -d {{ domain_name }} \
      --non-interactive \
      --agree-tos \
      -m {{ nginx_ssl_email }}
  args:
    creates: /etc/letsencrypt/live/{{ domain_name }}/fullchain.pem
  tags: ssl

- name: Deploy full Nginx config with SSL
  ansible.builtin.template:
    src: craft-dashboard.conf.j2
    dest: /etc/nginx/sites-available/craft-dashboard
    mode: "0644"
  notify: Reload Nginx
  tags: ssl

- name: Set up certbot renewal cron
  ansible.builtin.cron:
    name: "certbot-renew"
    hour: "3"
    minute: "30"
    job: "certbot renew --quiet --post-hook 'systemctl reload nginx'"
  tags: ssl
```

- [ ] **Step 3: Create `provisioning/roles/nginx/handlers/main.yml`**

```yaml
---
- name: Reload Nginx
  ansible.builtin.systemd:
    name: nginx
    state: reloaded
```

- [ ] **Step 4: Commit**

```bash
git add provisioning/roles/nginx/
git commit -m "feat: add Nginx Ansible role with Let's Encrypt SSL"
```

---

### Task 6: Cron Role — Systemd Timers

**Files:**
- Create: `provisioning/roles/cron/tasks/main.yml`
- Create: `provisioning/roles/cron/templates/collect-data.service.j2`
- Create: `provisioning/roles/cron/templates/collect-data.timer.j2`
- Create: `provisioning/roles/cron/templates/run-llm.service.j2`
- Create: `provisioning/roles/cron/templates/run-llm.timer.j2`

- [ ] **Step 1: Create systemd service for data collection**

Create `provisioning/roles/cron/templates/collect-data.service.j2`:
```ini
[Unit]
Description=craft-dashboard data collection
After=network.target postgresql.service

[Service]
Type=oneshot
User={{ app_user }}
Group={{ app_group }}
WorkingDirectory={{ app_dir }}
EnvironmentFile={{ app_dir }}/.env
ExecStart={{ app_venv }}/bin/python scripts/collect_data.py --source all
StandardOutput=journal
StandardError=journal
```

- [ ] **Step 2: Create systemd timer for data collection**

Create `provisioning/roles/cron/templates/collect-data.timer.j2`:
```ini
[Unit]
Description=Daily data collection for craft-dashboard

[Timer]
OnCalendar={{ collect_schedule }}
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create systemd service for LLM evaluation**

Create `provisioning/roles/cron/templates/run-llm.service.j2`:
```ini
[Unit]
Description=craft-dashboard LLM evaluation
After=network.target postgresql.service collect-data.service

[Service]
Type=oneshot
User={{ app_user }}
Group={{ app_group }}
WorkingDirectory={{ app_dir }}
EnvironmentFile={{ app_dir }}/.env
ExecStart={{ app_venv }}/bin/python scripts/run_llm.py --open-only
StandardOutput=journal
StandardError=journal
```

- [ ] **Step 4: Create systemd timer for LLM evaluation**

Create `provisioning/roles/cron/templates/run-llm.timer.j2`:
```ini
[Unit]
Description=Daily LLM evaluation for craft-dashboard

[Timer]
OnCalendar={{ llm_schedule }}
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Create `provisioning/roles/cron/tasks/main.yml`**

```yaml
---
- name: Deploy collect-data service
  ansible.builtin.template:
    src: collect-data.service.j2
    dest: /etc/systemd/system/collect-data.service
    mode: "0644"
  notify: Reload systemd

- name: Deploy collect-data timer
  ansible.builtin.template:
    src: collect-data.timer.j2
    dest: /etc/systemd/system/collect-data.timer
    mode: "0644"
  notify: Reload systemd

- name: Deploy run-llm service
  ansible.builtin.template:
    src: run-llm.service.j2
    dest: /etc/systemd/system/run-llm.service
    mode: "0644"
  notify: Reload systemd

- name: Deploy run-llm timer
  ansible.builtin.template:
    src: run-llm.timer.j2
    dest: /etc/systemd/system/run-llm.timer
    mode: "0644"
  notify: Reload systemd

- name: Enable and start collect-data timer
  ansible.builtin.systemd:
    name: collect-data.timer
    state: started
    enabled: true

- name: Enable and start run-llm timer
  ansible.builtin.systemd:
    name: run-llm.timer
    state: started
    enabled: true
```

- [ ] **Step 6: Create `provisioning/roles/cron/handlers/main.yml`**

Create `provisioning/roles/cron/handlers/main.yml`:
```yaml
---
- name: Reload systemd
  ansible.builtin.systemd:
    daemon_reload: true
```

- [ ] **Step 7: Commit**

```bash
git add provisioning/roles/cron/
git commit -m "feat: add cron Ansible role with systemd timers"
```

---

### Task 6b: Database Backup

**Files:**
- Create: `provisioning/roles/cron/templates/backup-db.service.j2`
- Create: `provisioning/roles/cron/templates/backup-db.timer.j2`
- Modify: `provisioning/roles/cron/tasks/main.yml`

The backup strategy is a daily `pg_dump` that saves a compressed dump to the VPS. Manual transfers to a local machine are done with `scp`. Automated off-site backups (e.g., to S3) can be added later.

- [ ] **Step 1: Create the backup service template**

Create `provisioning/roles/cron/templates/backup-db.service.j2`:
```ini
[Unit]
Description=craft-dashboard PostgreSQL backup
After=postgresql.service

[Service]
Type=oneshot
User=postgres
ExecStart=/bin/bash -c 'pg_dump {{ db_name }} | gzip > {{ app_dir }}/backups/craft-dashboard-$(date +%%Y%%m%%d).sql.gz'
StandardOutput=journal
StandardError=journal
```

- [ ] **Step 2: Create the backup timer template**

Create `provisioning/roles/cron/templates/backup-db.timer.j2`:
```ini
[Unit]
Description=Daily PostgreSQL backup for craft-dashboard

[Timer]
OnCalendar=*-*-* 01:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Add backup directory and systemd units to cron role**

In `provisioning/roles/cron/tasks/main.yml`, add before the "Reload systemd" handler call:
```yaml
- name: Create backup directory
  ansible.builtin.file:
    path: "{{ app_dir }}/backups"
    state: directory
    owner: postgres
    group: "{{ app_group }}"
    mode: "0750"

- name: Deploy backup-db service
  ansible.builtin.template:
    src: backup-db.service.j2
    dest: /etc/systemd/system/backup-db.service
    mode: "0644"
  notify: Reload systemd

- name: Deploy backup-db timer
  ansible.builtin.template:
    src: backup-db.timer.j2
    dest: /etc/systemd/system/backup-db.timer
    mode: "0644"
  notify: Reload systemd

- name: Enable and start backup-db timer
  ansible.builtin.systemd:
    name: backup-db.timer
    state: started
    enabled: true
```

- [ ] **Step 4: Add backup retention (keep last 7 days)**

Add to the backup service `ExecStart` — or add a separate `ExecStartPost`:
```ini
ExecStartPost=/bin/bash -c 'find {{ app_dir }}/backups -name "*.sql.gz" -mtime +7 -delete'
```

- [ ] **Step 5: Document manual backup retrieval**

Add to `README.md` under `### Manual Operations`:
```bash
# Copy latest backup to your local machine
scp ubuntu@your-vps:/opt/craft-dashboard/backups/craft-dashboard-$(date +%Y%m%d).sql.gz ~/backups/

# Or copy all backups
scp -r ubuntu@your-vps:/opt/craft-dashboard/backups/ ~/backups/craft-dashboard/

# Restore a backup locally (for testing)
gunzip -c ~/backups/craft-dashboard-20260101.sql.gz | psql craft_dashboard
```

- [ ] **Step 6: Commit**

```bash
git add provisioning/roles/cron/
git commit -m "feat: add daily PostgreSQL backup with 7-day retention"
```

---

### Task 7: Main Playbook

**Files:**
- Create: `provisioning/playbook.yml`

- [ ] **Step 1: Create `provisioning/playbook.yml`**

```yaml
---
- name: Provision craft-dashboard
  hosts: dashboard
  become: true

  pre_tasks:
    - name: Validate required environment variables
      ansible.builtin.assert:
        that:
          - lookup('env', 'DB_PASSWORD') | length > 0
          - lookup('env', 'GITHUB_TOKEN') | length > 0
        fail_msg: >
          Required environment variables are not set.
          Ensure DB_PASSWORD and GITHUB_TOKEN are defined.
          Optional: OPENROUTER_API_KEY, ADMIN_TOKEN, DOMAIN_NAME, SSL_EMAIL.

  roles:
    - common
    - postgresql
    - app
    - nginx
    - cron

  post_tasks:
    - name: Verify craft-dashboard is running
      ansible.builtin.uri:
        url: "http://127.0.0.1:{{ app_port }}/health"
        return_content: true
      register: health_check
      until: health_check.status == 200
      retries: 5
      delay: 3

    - name: Display deployment status
      ansible.builtin.debug:
        msg: |
          craft-dashboard deployed successfully!
          Health check: {{ health_check.json }}
          URL: https://{{ domain_name }}
```

- [ ] **Step 2: Commit**

```bash
git add provisioning/playbook.yml
git commit -m "feat: add main Ansible playbook for full VPS provisioning"
```

---

### Task 8: Deployment Documentation

**Files:**
- Modify: `README.md` (add deployment and LXD testing sections)

- [ ] **Step 1: Add deployment and LXD VM testing sections to README.md**

Append to `README.md`:
```markdown

## Deployment

### Prerequisites

- A VPS running Ubuntu 24.04 LTS (or an LXD VM for testing — see below)
- SSH access to the target
- Ansible 2.16+ installed locally
- A domain name pointing to the VPS IP (not needed for LXD testing)

### Environment Variables

Create a file with your deployment secrets and source it before running Ansible:

```bash
export DASHBOARD_HOST=your-vps-ip
export DASHBOARD_USER=ubuntu
export DASHBOARD_SSH_KEY=~/.ssh/id_ed25519
export DB_PASSWORD=your-secure-database-password
export GITHUB_TOKEN=your-github-token
export OPENROUTER_API_KEY=your-openrouter-key  # Optional
export ADMIN_TOKEN=your-admin-token             # Optional
export DOMAIN_NAME=dashboard.example.com
export SSL_EMAIL=admin@example.com
```

### Running the Playbook

```bash
cd provisioning
ansible-playbook playbook.yml
```

### Re-deploying

The playbook is idempotent. Run it again to update:

```bash
cd provisioning
ansible-playbook playbook.yml
```

This will:
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
```

### Initial LLM Pass with Local LLM Server (Recommended)

Before deploying to the VPS, run a full LLM evaluation using your local LLM server to
avoid spending money on OpenRouter for the first pass of all historical issues.

**Step 1: Collect data locally (into your local PostgreSQL)**

```bash
export DATABASE_URL=postgresql+asyncpg://localhost/craft_dashboard_local
export GITHUB_TOKEN=your-github-token
uv run scripts/collect_data.py --source all
```

**Step 2: Run the full LLM evaluation against your local server**

```bash
export LLM_BACKEND=local
export LOCAL_LLM_URL=http://192.168.1.x:port/v1   # your server's address
export LOCAL_LLM_SUMMARY_MODEL=your-model-name
export LOCAL_LLM_EVALUATION_MODEL=your-model-name

uv run scripts/run_llm.py  # evaluates all issues (open and closed)
```

This may take several hours depending on the number of issues and model speed.

**Step 3: Export and import to the VPS**

```bash
# Dump the local database (includes collected data + LLM evaluations)
pg_dump craft_dashboard_local | gzip > craft-dashboard-initial.sql.gz

# Copy to VPS
scp craft-dashboard-initial.sql.gz ubuntu@your-vps:~

# Restore on VPS (before running the Ansible playbook, or after with app stopped)
ssh ubuntu@your-vps "gunzip -c craft-dashboard-initial.sql.gz | sudo -u postgres psql craft_dashboard"
```

After this, the VPS has a fully-evaluated dataset. The daily OpenRouter cron job
(`run_llm.py --open-only`) then only processes newly-changed open issues incrementally.

## Testing with LXD VM

You can test the full deployment locally using an LXD virtual machine. This gives
you a real Ubuntu 24.04 environment identical to the production VPS.

### Prerequisites

Install LXD if not already available:

```bash
sudo snap install lxd
lxd init --auto  # Accept defaults for local testing
```

### Launch a Test VM

```bash
# Launch an Ubuntu 24.04 VM (not a container — VM gives full systemd support)
lxc launch ubuntu:24.04 craft-dashboard-test --vm

# Wait for the VM to finish cloud-init (~30 seconds)
lxc exec craft-dashboard-test -- cloud-init status --wait

# Get the VM's IP address
VM_IP=$(lxc list craft-dashboard-test --format csv -c 4 | cut -d' ' -f1)
echo "VM IP: $VM_IP"
```

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

### Deploy to the LXD VM

```bash
# Set environment variables for LXD deployment
export DASHBOARD_HOST=$VM_IP
export DASHBOARD_USER=ubuntu
export DB_PASSWORD=test-password-123
export GITHUB_TOKEN=your-github-token
export DOMAIN_NAME=localhost  # No real domain needed for testing

# Run the playbook (skip SSL since we don't have a real domain)
cd provisioning
ansible-playbook playbook.yml --skip-tags ssl
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
lxc exec craft-dashboard-test -- sudo -u postgres psql -c "\\l" | grep craft_dashboard
```

### Iterate on Changes

```bash
# Re-run the playbook after code changes (idempotent)
cd provisioning
ansible-playbook playbook.yml --skip-tags ssl

# Or just restart the app after local changes pushed to the repo
lxc exec craft-dashboard-test -- systemctl restart craft-dashboard
```

### Tear Down

```bash
# Delete the VM when done
lxc delete craft-dashboard-test --force
```

### Tips

- Use `--vm` flag (not plain `lxc launch`) to get full systemd support. Containers
  don't support systemd services and timers reliably.
- Skip the `ssl` tag for local testing since certbot needs a real domain.
- The VM is ephemeral — re-create it any time for a clean test.
- Forward a port if you prefer accessing via localhost:
  ```bash
  lxc config device add craft-dashboard-test dashboard proxy \
    listen=tcp:0.0.0.0:8080 connect=tcp:127.0.0.1:8000
  # Then access http://localhost:8080
  ```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add deployment and LXD VM testing documentation"
```

---

### Task 9: Verify Provisioning Structure

**Files:**
- No new files

- [ ] **Step 1: Verify all Ansible files are syntactically valid**

Run from the provisioning directory:
```bash
cd provisioning && ansible-playbook playbook.yml --syntax-check
```
Expected: Syntax check passes (may warn about missing host, which is fine)

- [ ] **Step 2: List the complete provisioning tree**

Run: `find provisioning -type f | sort`
Expected output:
```
provisioning/ansible.cfg
provisioning/group_vars/all.yml
provisioning/inventory.yml
provisioning/playbook.yml
provisioning/roles/app/handlers/main.yml
provisioning/roles/app/tasks/main.yml
provisioning/roles/app/templates/craft-dashboard.service.j2
provisioning/roles/app/templates/env.j2
provisioning/roles/common/handlers/main.yml
provisioning/roles/common/tasks/main.yml
provisioning/roles/cron/handlers/main.yml
provisioning/roles/cron/tasks/main.yml
provisioning/roles/cron/templates/collect-data.service.j2
provisioning/roles/cron/templates/collect-data.timer.j2
provisioning/roles/cron/templates/run-llm.service.j2
provisioning/roles/cron/templates/run-llm.timer.j2
provisioning/roles/nginx/handlers/main.yml
provisioning/roles/nginx/tasks/main.yml
provisioning/roles/nginx/templates/craft-dashboard.conf.j2
provisioning/roles/postgresql/handlers/main.yml
provisioning/roles/postgresql/tasks/main.yml
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: complete provisioning structure"
```
