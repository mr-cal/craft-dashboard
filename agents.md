# agents.md

## Before completing any task

Before completing any task, run and ensure the following pass:

```bash
make format
make lint
make test
```

Existing failures should be noted and communicated to the user.

Any changes to the Dockerfile or Alembic migrations should also verify that
`make build` succeeds.

Finally, your changes should be commited and pushed.

## Before completing UI/UX tasks

Run the e2e tests when making UI or UX changes:

```bash
make test-e2e  # ~5-10 min
```

## Production

The VPS for this project is managed by the `mr-cal/vps-infra` repo on github.
When you push to `mr-cal/dashboard`, the vps-infra will pick up the newly

Don't change the configured git url for origin when pushing and pulling changes.
Instead, just push to a custom url with the token.

## Key config files

- `craft-dashboard.toml` - project list, maintainers, bots, hotfix thresholds.
- `.env` - runtime secrets and feature flags. Not committed.
- `.env.llm` - information for connecting to the server for debugging and pushing
  changes to git repos.
- `alembic/versions/` - database migrations. Always generate with
  `uv run alembic revision --autogenerate -m "<description>"`.

## Database

Schema is managed by Alembic. The app runs `alembic upgrade head` on every
startup, so migrations apply automatically on deploy.

## Image publishing

Pushing to `main` triggers `.github/workflows/publish.yml`, which builds and
pushes `ghcr.io/mr-cal/craft-dashboard:latest` to GHCR, then dispatches a
`repository_dispatch` event to [mr-cal/vps-infra](https://github.com/mr-cal/vps-infra)
to trigger a redeploy. The vps-infra deploy workflow pulls the new image and restarts
the container. You should verify the deployment job succeeded after pushing commits.

A `VPSINFRA_PAT` secret must be set on this repo (Settings → Secrets and variables →
Actions) with a fine-grained PAT scoped to `mr-cal/vps-infra` with
**Contents: Read and write**.
