# ---- Build stage ----
FROM python:3.12-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY craft_dashboard/ craft_dashboard/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/
COPY craft-dashboard.toml ./

# setuptools_scm needs .git; use a pretend version in Docker builds
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0

# Install the app and its dependencies into a virtual environment
RUN uv venv /app/.venv \
    && uv pip install --python /app/.venv/bin/python . psycopg2-binary

# ---- Runtime stage ----
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 postgresql-client git \
    && rm -rf /var/lib/apt/lists/*

# A fixed UID so bind-mounted host directories (the git mirror store) can be
# chowned to match. Keep in sync with vps-infra's mirror volume ownership.
RUN useradd --create-home --uid 10001 --user-group app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /build /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app

RUN chown -R app:app /app

USER app

EXPOSE 8000

# Declared here as well as in compose, so `podman run` and any orchestrator
# that ignores compose still get a liveness signal. curl is not installed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["/usr/local/bin/docker-entrypoint.sh"]
