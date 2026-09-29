# craft-dashboard

craft-dashboard is a FastAPI dashboard for the \*craft project family. It collects issue, pull request, release, dependency, Launchpad, and Discourse forum data into PostgreSQL, runs LLM evaluations through an HTTP pull API, and renders project health views with Jinja2 templates.

## Quick start

From the repository root:

```bash
make setup
cp .env.example .env
podman compose up --build
```

The local app listens at `http://localhost:8000/`. The Compose stack starts PostgreSQL and runs Alembic migrations during app startup.

Useful checks:

```bash
make format
make lint
make test
```

## Documentation

| File | Use it for |
|---|---|
| [Development](docs/development.md) | Local setup, running the app, tests, linting, and single-test commands. |
| [Operations](docs/operations.md) | Collection, evaluation, backups, project changes, mirror sync, and deployment recipes. |
| [Reference](docs/reference.md) | Environment variables, commands, schedules, tables, and HTTP endpoints. |
| [Architecture](docs/architecture.md) | Data flow, evaluation design, versioning, and deployment shape. |
