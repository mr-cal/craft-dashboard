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

Then get an `EVAL_API_TOKEN` from the server admin. The token authorizes access to the `/api/eval/*` endpoints used by the client.

## Usage

Run the client from the repository root.

```bash
# Evaluate 10 issues from the local Ollama
python scripts/eval_client.py \
  --server https://craft-dashboard.example.com \
  --token <eval-api-token> \
  --model llama3.2 \
  --llm-url http://localhost:11434/v1 \
  --limit 10

# Evaluate using a remote LLM with TLS
python scripts/eval_client.py \
  --server https://craft-dashboard.example.com \
  --token <eval-api-token> \
  --model Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf \
  --llm-url https://192.168.1.64:8443/v1 \
  --llm-api-key <llm-api-key> \
  --ca-cert ~/.config/local-llm/cert.pem \
  --limit 50 \
  --open-only
```

By default the client polls every 30 seconds when no work is available and continues until you stop it. Use `--limit` for bounded runs.

## CLI options

| Option | Purpose |
| --- | --- |
| `--server` | Base URL of the craft-dashboard server |
| `--token` | Bearer token for the eval API |
| `--model` | Model name recorded with submitted evaluations |
| `--llm-url` | OpenAI-compatible LLM endpoint |
| `--llm-api-key` | API key for the LLM endpoint, if required |
| `--ca-cert` | CA certificate for verifying the LLM server over TLS |
| `--poll-interval` | Seconds to wait before polling again when the queue is empty |
| `--limit` | Maximum evaluations before exit; `0` means unlimited |
| `--project` | Restrict work to a single project |
| `--open-only` / `--all-issues` | Evaluate only open issues or include closed ones |
| `--force` | Re-evaluate even if the current content hash already matches |
| `--incomplete` | Only pull issues with missing or partial evaluations |
| `--stale-days` | Only pull evaluations older than `N` days |
| `--server-ca-cert` | CA certificate for verifying the craft-dashboard server over TLS |

## Architecture note

Evaluation is now pull-based:

- `GET /api/eval/next` returns the next issue to evaluate,
- `POST /api/eval/result` stores the finished evaluation,
- `GET /api/eval/status` reports queue progress,
- the client initiates every connection over HTTPS,
- the server never opens an outbound connection to your machine or local LLM.

This is more secure for local and home-lab setups because you do not need to expose your developer machine or LLM endpoint to the public internet.
