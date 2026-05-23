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
make deploy-vm     # deploy to an LXD VM (see docs/deployment.md)
```

## Documentation

- [Development](docs/development.md) -- local setup, tests, linting, project layout
- [Deployment](docs/deployment.md) -- LXD VM and VPS deployment with Ansible
- [Architecture](docs/architecture.md) -- how the app works, data flow, schema
- [How-to guide](docs/how-to.md) -- scripts, common operations, recipes
