# Eval client

## Overview

The eval client is a local worker for pull-based issue evaluation. It has two phases:

**Phase 1 – evaluate:** pulls issues from the server, runs them through your local LLM (summarization and scoring), and pushes results back.

**Phase 2 – detect-duplicates:** runs after phase 1 is complete. Computes vector embeddings for each summary, finds near-neighbours via the server, and uses the LLM to confirm duplicates. Requires pgvector on the server.

## Prerequisites

- Python 3.12+
- Access to an OpenAI-compatible LLM endpoint (for example Ollama or llama-server)
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

### TLS certificates

There are two separate TLS cert settings:

| Variable | Flag | Purpose |
|---|---|---|
| `EVAL_CLIENT_SERVER_CA_CERT` | `--server-ca-cert` | CA cert to verify the **craft-dashboard server** |
| `LOCAL_LLM_CA_CERT` | `--ca-cert` | CA cert to verify the **local LLM endpoint** |

**`EVAL_CLIENT_SERVER_CA_CERT`** — you only need this if the craft-dashboard server
uses a self-signed or private CA certificate. If the server uses a publicly-trusted
cert (e.g. Let's Encrypt, as `craft-dashboard.name` does), leave this unset and the
system CA bundle handles verification automatically.

To get the server's CA cert from a running server:

```bash
# Fetch the CA cert from the server (replace with your server hostname)
openssl s_client -connect craft-dashboard.name:443 -showcerts </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > server-ca.pem
```

For a self-hosted server that uses a custom CA, ask the server admin for the CA cert
PEM file, or copy it from the server directly (e.g. from `/etc/ssl/certs/` or
wherever the CA cert is stored).

**`LOCAL_LLM_CA_CERT`** — needed when your LLM endpoint uses HTTPS with a self-signed
cert (common for llama-server on a local machine). See the usage example below.

## Usage

Run the client from the repository root:

```bash
# Evaluate 10 open issues using the local Ollama
uv run scripts/eval_client.py evaluate --limit 10

# Evaluate open issues for a specific project with a remote LLM
uv run scripts/eval_client.py evaluate --project snapcraft --open-only \
  --summary-model Qwen3-35B --evaluation-model Qwen3-35B \
  --llm-url https://192.168.1.64:8443/v1 \
  --ca-cert ~/.config/local-llm/cert.pem

# After phase 1 is complete, detect duplicates
uv run scripts/eval_client.py detect-duplicates
```

By default phase 1 polls every 30 seconds when no work is available and
continues until you stop it. Use `--limit` for bounded runs.

For the full list of options and their corresponding environment variables:

```bash
uv run scripts/eval_client.py --help
uv run scripts/eval_client.py evaluate --help
uv run scripts/eval_client.py detect-duplicates --help
```

## Phase 2: duplicate detection

Phase 2 requires:

1. **pgvector** installed on the PostgreSQL server
2. An **embedding model** served at the same `LOCAL_LLM_URL` endpoint

Set `LOCAL_LLM_EMBEDDING_MODEL` in your `.env` to the embedding model name. For example:

```ini
LOCAL_LLM_EMBEDDING_MODEL=nomic-embed-text
```

The `evaluate` subcommand computes embeddings during phase 1 if `LOCAL_LLM_EMBEDDING_MODEL` is set (recommended). Phase 2 then only needs to run LLM confirmation — it does not re-embed.

Use `--cosine-threshold` to tune how aggressively candidates are selected. A lower value (e.g. `0.10`) is more conservative; a higher value (e.g. `0.25`) finds more candidates to compare. The default is `0.15`.

## Architecture note

Evaluation is pull-based:

- `GET /api/eval/next` returns the next issue to evaluate,
- `POST /api/eval/result` stores the finished evaluation,
- `GET /api/eval/duplicate-work` returns the next batch for phase 2,
- `POST /api/eval/similar` finds near-neighbours by cosine distance,
- `POST /api/eval/duplicate-result` stores the phase-2 result,
- `GET /api/eval/status` reports queue progress,
- the client initiates every connection over HTTPS,
- the server never opens an outbound connection to your machine or local LLM.

This is more secure for local and home-lab setups because you do not need to expose your developer machine or LLM endpoint to the public internet.

