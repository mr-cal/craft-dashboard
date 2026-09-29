# agents.md

Guidance for AI agents working in this repository. Read this before making changes.

## What this is

A dashboard over the Canonical "craft" tooling projects. It collects issues, pull
requests, releases, and forum activity from GitHub, Launchpad, and Discourse into
PostgreSQL, runs LLM evaluations over that data, and serves a FastAPI + Jinja2 site.

Collection is offline and scheduled; the web app only reads what collection has
already stored. See `docs/architecture.md` for why it is shaped this way.

## Repo map

| Path | What lives here |
|---|---|
| `craft_dashboard/routes/` | FastAPI route handlers, one module per page |
| `craft_dashboard/repositories/` | Database queries; all SQL belongs here |
| `craft_dashboard/services/` | Business logic that spans repositories |
| `craft_dashboard/collectors/` | External data collection (GitHub, Launchpad, Discourse) |
| `craft_dashboard/llm/` | LLM clients, prompts, evaluation, embeddings |
| `craft_dashboard/models/` | SQLAlchemy ORM models |
| `craft_dashboard/templates/` | Jinja2 templates (Vanilla Framework) |
| `craft_dashboard/static/` | CSS, JS, and pinned vendored browser libraries |
| `craft_dashboard/http_retry.py` | Shared retry policy for all outbound HTTP |
| `scripts/` | CLI entry points and one-off backfills |
| `alembic/versions/` | Database migrations |
| `tests/unit/`, `tests/integration/` | Run by `make test` |
| `tests/end_to_end/` | Run by `make test-e2e`, needs a browser |

Layering runs routes → services → repositories → models. Routes must not build
queries directly, and collectors must not import from routes. `import-linter`
contracts in `pyproject.toml` enforce this; `make lint` runs them.

`REPO_MAP.md` has the fuller map, including where each decision is configured.
`craft_dashboard/{routes,services,repositories,collectors,llm}/AGENTS.md` and
`scripts/llm/AGENTS.md` each state that package's public API, invariants,
forbidden imports, and one-test command. Read the one covering the code you are
about to change.

## Before completing any task

```bash
make check     # format + lint + fast unit tests, the usual inner loop
make test      # the full suite, before you call the task done
```

`make check` expands to `make format`, `make lint`, and `make test-fast`. Both must
pass. Report pre-existing failures rather than fixing unrelated code.

`make lint` also runs `lint-imports`, which enforces the layering above from the
contracts in `pyproject.toml`. If it fails, move the shared code down a layer rather
than relaxing the contract.

Additionally:

| If you changed | Also run |
|---|---|
| Templates, CSS, or JS | `make test-e2e` (about 6 minutes, needs a browser) |
| `Dockerfile` or `alembic/versions/` | `make build` |
| `docs/`, `README.md`, or this file | `uv run python scripts/check_docs.py` |

Add a regression test for every bug you fix. A fix without a test that fails before
it is not finished.

## Writing code here

- Comment *why*, not *what*. Explain the constraint or failure mode that makes the
  code look the way it does. Do not narrate what the next line does.
- Do not write comments that reference the change you are making ("now uses",
  "previously", "fixed to"). The git log covers that; a comment should read correctly
  to someone who never saw the old version.
- Put SQL in `repositories/`. Put rendering in templates. Put outbound HTTP retry
  behaviour in `http_retry.py`.
- Never interpolate untrusted content into HTML. Use `| tojson` for data embedded in
  `<script>`, and sanitize any Markdown rendered into `innerHTML`. Issue bodies come
  from anyone who can file an issue on a tracked repo.
- Frontend dependencies are vendored under `static/vendor/` and pinned. Do not add a
  CDN `<script>` tag.

## Writing docs here

`docs/` is linted by `scripts/check_docs.py`. The rules:

1. Present tense, current state only. No "no longer", "previously", "used to".
2. No project-management references: no "Phase N", "Task N", no commit SHAs from
   other repos.
3. No self-justification. State the procedure; do not argue that it is trustworthy.
4. No root-cause essays. Those belong in code comments or commit messages.
5. Facts live in exactly one place. `docs/reference.md` is authoritative.
6. No hardcoded hosts, IPs, or container names. Use the shell variables defined at
   the top of `docs/operations.md`.
7. No volatile numbers: no item counts, RAM measurements, or dated benchmarks.
8. Commands must run as written from a stated working directory.

## Local development

```bash
make setup      # install dependencies
make test-fast  # unit tests in parallel, seconds
make test       # unit + integration, no external services needed
make check      # format, lint, and fast tests — the pre-commit gate
```

Unit and integration tests run against in-memory SQLite and need no credentials.
Reach for them first: they are faster than anything involving a container.

To look at a UI change in a browser, use the seeded stack rather than deploying:

```bash
make dev-seeded  # builds the image, starts postgres + app, loads fixture data
make dev-down    # stop it and delete the volumes
```

It prints a `http://localhost:PORT` URL and uses the same fixtures as the
end-to-end suite, so what you see matches what `make test-e2e` asserts. Never
verify a change by deploying it to production first.

## Key config files

| File | Contents | Committed |
|---|---|---|
| `craft-dashboard.toml` | Project list, maintainers, bots, forums, thresholds | Yes |
| `.env` | Runtime secrets and feature flags | No |
| `.env.llm` | Credentials for the VPS and for pushing to GitHub | No |

Connectivity to the LLM server and the production web server is expected to work
using the values in `.env`. If it does not, stop and ask rather than working around
it.

## Database

Alembic owns the schema. The app runs `alembic upgrade head` at startup, so
migrations apply on deploy. Generate one with:

```bash
uv run alembic revision --autogenerate -m "<description>"
```

Review the generated migration before committing it; autogenerate misses type
changes and server defaults.

## Deploying

**Deploy only when the task calls for it.** Most changes are finished once
`make lint` and `make test` pass. Push and deploy when the user asks for it, when
the change must be verified against production data, or when it fixes something
currently broken in production.

Pushing to `main` triggers `.github/workflows/publish.yml`, which builds and pushes
`ghcr.io/mr-cal/craft-dashboard:latest` to GHCR, then sends a `repository_dispatch`
event to [mr-cal/vps-infra](https://github.com/mr-cal/vps-infra), whose deploy
workflow pulls the image and restarts the container.

Leave `origin` alone. Push to a URL carrying an ephemeral token instead:

```bash
git push "$(../../cal/vps-infra/scripts/mint_bot_token.py --print-remote-url)" main
```

That minter uses the GitHub App credentials in `.env.llm` (`GITHUB_APP_ID`,
`GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`) to issue a one-hour
token scoped to this repository, falling back to `GH_TOKEN`. Setup is documented in
`vps-infra/docs/github-app-auth.md`.

After pushing, confirm the publish workflow succeeded, then the vps-infra deploy
workflow, then check the change on the live site. The repo needs a `VPSINFRA_PAT`
secret scoped to `mr-cal/vps-infra` with **Contents: Read and write** for the
dispatch to work.

Operational recipes — running collection by hand, restoring a backup, inspecting the
database — are in `docs/operations.md`.

## Touching production data

Back up before any bulk write:

```bash
podman exec -i "$DB_CONTAINER" pg_dump -U craft_dashboard craft_dashboard \
  | gzip > "backup-$(date +%Y%m%d-%H%M).sql.gz"
```

Prefer a supported CLI flag over editing rows by hand. If no flag exists, add one —
a repair you can only perform with ad-hoc SQL is a repair nobody can repeat.
