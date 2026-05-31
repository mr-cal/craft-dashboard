# agents.md

## Before completing any task

Always run:

```bash
make format   # ruff format + ruff check --fix
make lint     # ruff check + ty check
make test     # pytest (unit + integration, ~495 tests)
```

Make sure all commands pass before marking a task complete.

Additionally, any changes to the docker file, setup (including alemic migrations)
should test that the docker compose build succeeds.

## Before completing UI/UX tasks

Always run the slow and e2e tasks when making UI or UX changes.

## Running all tests

### Unit and integration tests (fast, ~15s)

```bash
make test                    # runs all non-marked tests
uv run pytest tests/unit/    # unit tests only
uv run pytest tests/integration/  # integration tests only
```

### End-to-end tests (requires Docker, ~5-10min)

E2E tests build the Docker image, start the app and PostgreSQL via Docker
Compose, seed with test data, and run Puppeteer-based browser tests.

**Prerequisites:**
- Docker Engine and Docker Compose plugin installed
- Node.js with puppeteer installed at `/tmp/node_modules/`

```bash
make test-e2e   # runs tests/end_to_end/ with Docker Compose
```

Or manually:
```bash
CRAFT_DASHBOARD_E2E=1 uv run pytest tests/end_to_end/ -v -x
```

### Running everything together

```bash
make format && make lint && make test && make test-e2e
```
