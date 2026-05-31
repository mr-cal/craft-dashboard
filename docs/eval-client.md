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

Get an `EVAL_API_TOKEN` from the server admin, then copy `.env.example` to `.env`
and fill in the eval client settings at the bottom of the file.
Source it before running the client:

```bash
source .env
```

The script also loads `.env` automatically, so sourcing is optional if you only
run via `uv run`.

## Usage

Run the client from the repository root:

```bash
# Evaluate 10 open issues using the local Ollama
uv run scripts/eval_client.py --limit 10

# Evaluate 50 open issues with a remote LLM over TLS
uv run scripts/eval_client.py --limit 50 --open-only \
  --summary-model Qwen3-35B --evaluation-model Qwen3-35B \
  --llm-url https://192.168.1.64:8443/v1 \
  --ca-cert ~/.config/local-llm/cert.pem
```

By default the client polls every 30 seconds when no work is available and
continues until you stop it. Use `--limit` for bounded runs.

For the full list of options and their corresponding environment variables:

```bash
uv run scripts/eval_client.py --help
```

## Architecture note

Evaluation is now pull-based:

- `GET /api/eval/next` returns the next issue to evaluate,
- `POST /api/eval/result` stores the finished evaluation,
- `GET /api/eval/status` reports queue progress,
- the client initiates every connection over HTTPS,
- the server never opens an outbound connection to your machine or local LLM.

This is more secure for local and home-lab setups because you do not need to expose your developer machine or LLM endpoint to the public internet.
