# Plan: VPS Infrastructure

Manage a Linode VPS that hosts multiple websites using Docker Compose and Caddy.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Linode VPS (Ubuntu 24.04)                                   │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Docker Compose Stack                                   │ │
│  │                                                         │ │
│  │  ┌───────────┐  ports 80/443                            │ │
│  │  │   Caddy   │◄──── internet                            │ │
│  │  │  (proxy)  │                                          │ │
│  │  └─────┬─────┘                                          │ │
│  │        │ routes by domain name                          │ │
│  │        ├──► craft-dashboard:8000 (FastAPI/Gunicorn)     │ │
│  │        ├──► wordpress:80       (future)                 │ │
│  │        ├──► static sites       (Caddy file_server)      │ │
│  │        └──► (more services as needed)                   │ │
│  │                                                         │ │
│  │  ┌────────────┐                                         │ │
│  │  │ PostgreSQL │                                         │ │
│  │  └────────────┘                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  Host: Docker, UFW, unattended-upgrades, SSH, cron           │
└──────────────────────────────────────────────────────────────┘
```

## Design Decisions

1. **Caddy over Nginx** — automatic HTTPS via Let's Encrypt, simpler config, no
   certbot cron needed. Handles TLS, certificate renewal, and HTTP→HTTPS redirects
   automatically.

2. **Static sites served directly by Caddy** — Caddy's `file_server` directive
   serves static files from mounted volumes. No separate containers needed.

3. **Single `docker-compose.yml`** — all services in one Compose file. One
   `docker compose up -d` runs everything.

4. **PostgreSQL in Docker** — named volumes for data persistence. Consistent with
   the rest of the stack.

## Repository: `mr-cal/vps-infra`

```
vps-infra/
├── docker-compose.yml          # All services
├── caddy/
│   └── Caddyfile               # Reverse proxy config for all domains
├── static-sites/
│   ├── egg-calculator/         # Copied by CI or git submodule
│   └── site2/                  # Static site #2 files
├── backups/
│   └── backup.sh               # DB backup script
├── .github/
│   └── workflows/
│       └── deploy.yml          # Deploy on push to main
├── .env.example                # Template for secrets
└── README.md
```

### docker-compose.yml

```yaml
services:
  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./caddy/Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
      - ./static-sites:/srv
    restart: unless-stopped

  craft-dashboard:
    image: ghcr.io/mr-cal/craft-dashboard:latest
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: craft_dashboard
      POSTGRES_USER: craft_dashboard
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U craft_dashboard"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  caddy_data:
  caddy_config:
  pgdata:
```

### Caddyfile

```caddyfile
dashboard.example.com {
    reverse_proxy craft-dashboard:8000
}

eggs.example.com {
    root * /srv/egg-calculator
    file_server
}

site2.example.com {
    root * /srv/site2
    file_server
}
```

Caddy auto-obtains and renews TLS certificates for all listed domains.

### Deploy Workflow

`.github/workflows/deploy.yml`:

```yaml
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to VPS
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/vps-infra
            git pull origin main
            docker compose pull
            docker compose up -d --remove-orphans
```

### Backup Script

`backups/backup.sh` — runs via host crontab:
- `docker compose exec -T postgres pg_dump ...` → gzip → `/opt/vps-infra/backups/`
- 14-day retention, delete older backups
- Optional: upload to Linode Object Storage ($5/mo for 250GB)

## VPS Setup (Manual, One-Time)

1. **Create Linode** — Ubuntu 24.04, recommended 4GB ($24/mo)
2. **Harden the server**
   - Create non-root user with sudo
   - Disable root SSH login, disable password auth (key-only)
   - `ufw allow 22,80,443/tcp && ufw enable`
   - Enable unattended-upgrades
   - Optional: fail2ban
3. **Install Docker**
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```
4. **Clone and start**
   ```bash
   git clone https://github.com/mr-cal/vps-infra.git /opt/vps-infra
   cd /opt/vps-infra
   cp .env.example .env  # edit with real secrets
   docker compose up -d
   ```
5. **GitHub Actions deploy key** — generate SSH keypair, add to VPS authorized_keys,
   add private key as GitHub Actions secret `VPS_SSH_KEY`

## Domain & DNS

- Keep domain registration on Namecheap
- Update A records for each domain to point to the Linode VPS IP address
- Caddy auto-obtains Let's Encrypt certs once DNS resolves — no manual SSL config

## Cron Jobs (Host Crontab)

```crontab
# Collect data daily at 2 AM
0 2 * * * cd /opt/vps-infra && docker compose exec -T craft-dashboard python scripts/collect_data.py --source all

# Run LLM evaluation daily at 6 AM
0 6 * * * cd /opt/vps-infra && docker compose exec -T craft-dashboard python scripts/run_llm.py evaluate --open-only

# Backup databases daily at 3 AM
0 3 * * * /opt/vps-infra/backups/backup.sh
```

## Day-to-Day DevOps Workflow

1. Push changes to `vps-infra` main → GitHub Actions SSHs to VPS, pulls, restarts
2. Push changes to `craft-dashboard` main → CI builds Docker image → pushes to GHCR
   → vps-infra deploy pulls new image (or trigger via webhook/workflow_dispatch)
3. Monitor: `docker compose logs -f <service>`, health checks auto-restart containers
4. Update images: `docker compose pull && docker compose up -d`

## Alternatives Considered

- **Cloudflare free tier** in front of Caddy — DDoS protection, caching, analytics.
  Can add later without architectural changes.
- **Linode Object Storage** for off-server backups — strongly recommended.
- **Ansible for VPS provisioning** — decided against; manual setup is simpler for a
  single server and Docker handles the application layer.
