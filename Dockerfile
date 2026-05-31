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

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /build /app
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Run Alembic migrations then start Gunicorn
CMD ["sh", "-c", "alembic upgrade head && gunicorn --bind 0.0.0.0:8000 --workers 4 --worker-class uvicorn.workers.UvicornWorker 'craft_dashboard.app:create_app()'"]
