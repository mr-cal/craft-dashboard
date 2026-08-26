# Evaluate worker

## Overview

`uv run scripts/run_llm.py evaluate` is the single continuous worker for
pull-based issue evaluation. It polls `GET /api/eval/next`, evaluates claimed
issues, computes a summary embedding, and submits the finished payload to
`POST /api/eval/result`.

The worker is HTTP-only: it never connects to PostgreSQL directly.

## Key behavior

- Runs continuously until stopped.
- Polls every 30 seconds by default (`--interval`).
- Runs 10 concurrent worker coroutines by default (`--concurrency`).
- Uses `--llm-backend openrouter|local` for evaluation text generation.
- Always computes embeddings through OpenRouter, even when `--llm-backend local`
  is selected.
- Always submits a non-null `summary_embedding`.
- There is no separate `embed` command anymore.

## Prerequisites

- Python 3.12+
- A clone of the `craft-dashboard` repository
- An `EVAL_API_TOKEN` from the craft-dashboard server administrator
- `OPENROUTER_API_KEY` (required for embeddings in all modes)
- For `--llm-backend local`: an OpenAI-compatible local LLM endpoint plus
  `LOCAL_LLM_URL` and `LOCAL_LLM_MODEL`

## Setup

Copy `.env.example` to `.env` and fill in the worker settings near the bottom
of the file. The command loads `.env` automatically.

### TLS certificates

There are two separate TLS cert settings:

| Variable | Flag | Purpose |
|---|---|---|
| `EVAL_CLIENT_SERVER_CA_CERT` | `--server-ca-cert` | CA cert to verify the **craft-dashboard server** |
| `LOCAL_LLM_CA_CERT` | `--ca-cert` | CA cert to verify the **local LLM endpoint** |

## Usage

Run the worker from the repository root:

```bash
# Continuous evaluation through OpenRouter
uv run scripts/run_llm.py evaluate --server https://craft-dashboard.name --token "$EVAL_API_TOKEN"

# Continuous evaluation through a local chat backend, with OpenRouter embeddings
uv run scripts/run_llm.py evaluate \
  --server https://craft-dashboard.name \
  --token "$EVAL_API_TOKEN" \
  --llm-backend local \
  --project snapcraft \
  --concurrency 4

# Bounded run for one project
uv run scripts/run_llm.py evaluate --server http://localhost:8000 --token "$EVAL_API_TOKEN" --limit 10
```

`--limit N` (aliased as `--max-evaluations N`, the name used in the deep-
evaluation design) doubles as the "bounded batch" safety rail used for staged
rollouts (e.g. a deep-evaluation pilot backfill): run `evaluate --limit 20`,
inspect the results, then re-run without `--limit` (or with a larger one) to
continue. No separate flag is needed for this.

Use `--issue` with `--project` to force one specific issue, and `--open-only`,
`--force`, `--incomplete`, or `--stale-days` to filter the HTTP queue.

For the full list of options:

```bash
uv run scripts/run_llm.py evaluate --help
```

## Architecture note

Evaluation is pull-based:

- `GET /api/eval/next` returns the next issue to evaluate,
- `POST /api/eval/result` stores the finished evaluation together with its
  OpenRouter embedding,
- `GET /api/eval/status` reports queue progress,
- the worker initiates every connection over HTTPS,
- the server never opens an outbound connection to your machine or local LLM.

This keeps local and home-lab setups simple: you can point the worker at the
public craft-dashboard API without exposing your developer machine.
