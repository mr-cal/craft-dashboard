# craft-dashboard

Dashboard, insights, and issue triage for the \*craft applications and libraries.

craft-dashboard collects GitHub and Launchpad data, runs LLM-based triage on
open issues, and presents the results as a filterable dashboard with trend
charts, release tracking, and dependency monitoring.

## Quick start

```
make setup         # install dependencies
make test          # run tests
make lint          # lint and type-check
```

## Docker

```
docker compose up --build   # run locally with Docker
```

## Documentation

| Doc | Description |
|-----|-------------|
| [Development](docs/development.md) | Local setup, tests, linting, project layout |
| [Deployment](docs/deployment.md) | Docker-based deployment and configuration |
| [Architecture](docs/architecture.md) | How the app works, data flow, schema |
| [How-to guide](docs/how-to.md) | Scripts, common operations, recipes |
| [Eval client](docs/eval-client.md) | Pull-based local LLM evaluation client |
