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

## Local evaluations

To evaluate issues and PRs:

```bash
uvx --env-file .env craft-dashboard eval status
uvx --env-file .env craft-dashboard eval run --concurrency 5
```

Minimum `.env` values:

```bash
DASHBOARD_URL=https://craft-dashboard.name

# ask @mr-cal for a token
EVAL_API_TOKEN=<token>

# Local OpenAI-compatible server:
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=...
LLM_MODEL=qwen/qwen3.8-27b

# Or OpenRouter:
# LLM_BASE_URL=https://openrouter.ai/api/v1
# LLM_API_KEY=sk-or-v1-...
# LLM_MODEL=qwen/qwen3.8-27b

# LLM & worker tuning
# LLM_TIMEOUT=600.0
# LLM_CA_CERT=path/to/cert.pem
# CRAFT_DASHBOARD_MIRROR_DIR=~/.cache/craft-dashboard/mirrors
# CRAFT_DASHBOARD_GIT_CONCURRENCY=2
```

Requires a mid-tier model with reasonable coding and reasoning capabilities. Lightweight
models (e.g. 'lite', 'mini', 'nano', and 'haiku') produce poor and misleading evaluations.

Example models that produce good evaluations include Claude Sonnet 4+, Qwen 3.6+,
Deepseek v4 Pro, GPT 5.5+, and Gemini 3.8+ Flash.


## Documentation

* [Development](docs/development.md)
* [Operations](docs/operations.md)
* [Reference](docs/reference.md)
* [Architecture](docs/architecture.md)
