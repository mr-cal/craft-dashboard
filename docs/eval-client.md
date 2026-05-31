# Eval client

## Overview

The eval client is a local worker for pull-based issue evaluation. It:

1. pulls the next issue from the craft-dashboard server,
2. evaluates it against your local or remote OpenAI-compatible LLM, and
3. pushes the result back to the server.

This keeps the LLM close to your machine while the server stays stateless and reachable over normal HTTPS.

## Prerequisites

- Python 3.12+
- Access to an OpenAI-compatible LLM endpoint (for example Ollama)
- A clone of the `craft-dashboard` repository
- An `EVAL_API_TOKEN` from the craft-dashboard server administrator

## Setup

From the repository root:

```bash
uv sync
```

Then get an `EVAL_API_TOKEN` from the server admin and set these environment
variables (for example in `.env` or your shell profile):

```bash
export EVAL_CLIENT_SERVER=https://craft-dashboard.example.com
export EVAL_API_TOKEN=<your-eval-api-token>
export LOCAL_LLM_URL=http://localhost:11434/v1   # default; change if needed
```

See [CLI options](#cli-options) for the full list of env var names.

## Usage

Run the client from the repository root.

```bash
# Evaluate 10 open issues using the local Ollama (env vars set)
python scripts/eval_client.py --limit 10

# Evaluate 50 open issues with a remote LLM over TLS
python scripts/eval_client.py --limit 50 --open-only \
  --summary-model Qwen3-35B --evaluation-model Qwen3-35B \
  --llm-url https://192.168.1.64:8443/v1 \
  --ca-cert ~/.config/local-llm/cert.pem
```

By default the client polls every 30 seconds when no work is available and continues until you stop it. Use `--limit` for bounded runs.

## CLI options

| Option | Env var | Purpose |
| --- | --- | --- |
| `--server` | `EVAL_CLIENT_SERVER` | Base URL of the craft-dashboard server |
| `--token` | `EVAL_API_TOKEN` | Bearer token for the eval API |
| `--summary-model` | `LOCAL_LLM_SUMMARY_MODEL` | Model name used for issue summarization |
| `--evaluation-model` | `LOCAL_LLM_EVALUATION_MODEL` | Model name used for scoring |
| `--llm-url` | `LOCAL_LLM_URL` | OpenAI-compatible LLM endpoint |
| `--llm-api-key` | `LOCAL_LLM_API_KEY` | API key for the LLM endpoint, if required |
| `--ca-cert` | `LOCAL_LLM_CA_CERT` | CA certificate for verifying the LLM server over TLS |
| `--server-ca-cert` | `EVAL_CLIENT_SERVER_CA_CERT` | CA certificate for verifying the craft-dashboard server over TLS |
| `--poll-interval` | — | Seconds to wait before polling again when the queue is empty |
| `--limit` | — | Maximum evaluations before exit; `0` means unlimited |
| `--project` | — | Restrict work to a single project |
| `--open-only` / `--all-issues` | — | Evaluate only open issues or include closed ones |
| `--force` | — | Re-evaluate even if the current content hash already matches |
| `--incomplete` | — | Only pull issues with missing or partial evaluations |
| `--stale-days` | — | Only pull evaluations older than `N` days |

## Architecture note

Evaluation is now pull-based:

- `GET /api/eval/next` returns the next issue to evaluate,
- `POST /api/eval/result` stores the finished evaluation,
- `GET /api/eval/status` reports queue progress,
- the client initiates every connection over HTTPS,
- the server never opens an outbound connection to your machine or local LLM.

This is more secure for local and home-lab setups because you do not need to expose your developer machine or LLM endpoint to the public internet.
