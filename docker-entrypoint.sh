#!/bin/sh
# Waits for PostgreSQL, applies migrations, then starts the app.
#
# Kept as a script rather than an inline CMD so the steps can be read, and so
# `docker run <image> <other command>` still works for one-off collector and
# backfill runs.
set -eu

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-craft_dashboard}"

until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" >/dev/null 2>&1; do
    echo "waiting for postgres at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
    sleep 2
done

alembic upgrade head

exec gunicorn \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-1}" \
    --worker-class uvicorn.workers.UvicornWorker \
    'craft_dashboard.app:create_app()'
