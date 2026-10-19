# craft-dashboard

**[craft-dashboard.name](https://craft-dashboard.name)** is a unified dashboard for
tracking work across Canonical's Starcraft tools (Snapcraft, Charmcraft, Rockcraft, and
core libraries).

Maintainers use it to monitor project health, catch aging pull requests, and flag
projects overdue for release. It runs LLM evaluations over incoming issues and PRs to
help prioritize triage, and tracks community engagement on Discourse.


## Local development

```bash
make setup
cp .env.example .env
podman compose up --build
```

The local app listens at `http://localhost:8000/`.

Useful targets:

```bash
make format
make lint
make test
```

## Documentation

* [Development](docs/development.md)
* [Operations](docs/operations.md)
* [Reference](docs/reference.md)
* [Architecture](docs/architecture.md)
