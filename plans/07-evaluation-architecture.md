# craft-dashboard — Evaluation Architecture Shift

## Motivation

craft-dashboard runs as a **Docker container** on a VPS. Currently, LLM evaluation runs
**inside the container**, connecting either to OpenRouter (cloud) or to a local LLM
(e.g. Ollama on a developer machine). The local LLM option requires the container to
reach the developer's machine — if the container is compromised, the attacker gets a
network path to the local LLM host.

Note: `localhost` inside the Docker container refers to the container itself, not the
host machine. Reaching a local Ollama requires `host.docker.internal` or a host IP
(e.g. `LOCAL_LLM_URL=https://192.168.1.10:8443/v1`), making the outbound path from
the container explicit and confirming the security risk.

**Goal:** Flip the evaluation direction. Instead of the container pushing work to an LLM,
a local script on the developer's machine **pulls** issues from the server's API,
evaluates them locally, and **pushes** results back. This eliminates the container→local
network path entirely.

## Current Architecture (Before)

```
┌──────────────────────────┐       ┌──────────────┐
│   Docker Container (VPS) │──────▶│  OpenRouter   │  (cloud LLM)
│   (FastAPI app)          │       └──────────────┘
│                          │
│   run_llm.py             │──────▶│  Local LLM   │  (developer machine — SECURITY RISK)
│   (cron/manual)          │       └──────────────┘
└──────────────────────────┘
```

- `scripts/llm/cli.py` — CLI entry point (`run_llm evaluate`)
- `scripts/llm/orchestrator.py` — fetches issues from DB, calls evaluator, stores results
- `craft_dashboard/llm/evaluator.py` — builds prompts, calls LLM client, parses response
- `craft_dashboard/llm/client.py` — `OpenRouterClient` and `LocalLLMClient`
- `craft_dashboard/settings.py` — `LLM_BACKEND` setting (`"openrouter"` or `"local"`)

## Target Architecture (After)

```
┌──────────────────────────┐                        ┌───────────────────┐
│   Docker Container (VPS) │◀── HTTPS (pull) ───────│  Local Machine    │
│   (FastAPI app)          │                        │                   │
│                          │── HTTPS (push result) ─│  eval-client.py   │
│   /api/eval/*            │                        │  + Local LLM      │
└──────────────────────────┘                        └───────────────────┘
                            │
                            │── (optional, toggled) ──▶  OpenRouter
```

### Key Changes

1. **New server-side API endpoints** (`/api/eval/...`) for pull-based evaluation
2. **New local client script** (`scripts/eval_client.py`) that polls the server
3. **Remove `LocalLLMClient`** from the server — the server never connects to a local LLM
4. **Add a toggle** to disable server-side OpenRouter evaluation (`ENABLE_SERVER_EVAL=false`)
5. **API authentication** for the eval endpoints (bearer token or HMAC)

## Detailed Design

### 1. Server-Side API Endpoints

New API router at `/api/eval/` with authentication required on all endpoints.

#### `GET /api/eval/next`

Returns the next issue needing evaluation, or 204 if none available.

Query parameters (mirror existing `run_llm` controls):
- `project` — filter by project name
- `open_only` — only open issues (default: true)
- `force` — ignore content hash (re-evaluate everything)
- `incomplete` — only issues with missing summary/scores
- `stale_days` — only issues whose evaluation is older than N days

Response (200):
```json
{
  "issue_id": 42,
  "project_name": "charmcraft",
  "external_id": "2687",
  "title": "Issue title",
  "state": "open",
  "issue_type": "issue",
  "body": "...",
  "comments": [...],
  "labels": [...],
  "author": "username",
  "author_association": "CONTRIBUTOR",
  "current_hash": "abc123...",
  "maintainers": ["user1", "user2"]
}
```

The server marks the issue as "locked for evaluation" (with a TTL, e.g. 10 minutes)
to prevent duplicate work if multiple clients poll.

#### `POST /api/eval/result`

Submit an evaluation result for an issue.

Request body:
```json
{
  "issue_id": 42,
  "content_hash": "abc123...",
  "summary": "This issue is about...",
  "scores": {
    "staleness": 3,
    "duplicateness": 1,
    "complexity": 4,
    "support_request": 2,
    "readiness": 5
  },
  "suggested_action": "keep_open",
  "suggested_action_reason": "Active development",
  "tokens_used": 1500,
  "prompt_tokens": 1200,
  "completion_tokens": 300,
  "model_used": "llama3.2",
  "llm_backend": "local"
}
```

The server validates the result (same validation as current `validate_evaluation_result`),
stores it, and returns 200. If the `content_hash` doesn't match the current issue hash
(issue was updated while being evaluated), the server returns 409 Conflict.

#### `GET /api/eval/status`

Returns evaluation queue status:
```json
{
  "pending": 150,
  "locked": 2,
  "evaluated_today": 48,
  "total_evaluated": 11053
}
```

### 2. Local Client Script (`scripts/eval_client.py`)

A standalone script that runs on the developer's machine:

```bash
# Poll the server for issues and evaluate them locally
python scripts/eval_client.py \
  --server https://craft-dashboard.example.com \
  --token <eval-api-token> \
  --model llama3.2 \
  --llm-url http://localhost:11434/v1 \
  --poll-interval 30 \
  --limit 100
```

Behavior:
1. Poll `GET /api/eval/next` with configured filters
2. If 204 (no work), sleep for `poll-interval` seconds
3. If 200, evaluate the issue using the local LLM
4. Submit result via `POST /api/eval/result`
5. Repeat until `--limit` reached or interrupted

The client reuses:
- `craft_dashboard/llm/evaluator.py` — prompt building and response parsing
- `craft_dashboard/llm/client.py` — `LocalLLMClient` (moved here or imported)
- `scripts/llm/validation.py` — result validation

### 3. Authentication

The eval API uses a dedicated bearer token, separate from the admin token:

```env
# .env (on the VPS, read by Docker via env_file in docker-compose.yml)
EVAL_API_TOKEN=<random-token>       # required for /api/eval/* endpoints
ENABLE_SERVER_EVAL=true             # set to false to disable server-side OpenRouter
OPENROUTER_API_KEY=<key>            # only needed if ENABLE_SERVER_EVAL=true
```

Server-side middleware checks `Authorization: Bearer <token>` on all `/api/eval/*`
routes.

### 4. Server-Side Evaluation Toggle

When `ENABLE_SERVER_EVAL=false`:
- The `run_llm evaluate` command on the server refuses to run (exits with message)
- The admin "Re-evaluate" button shows a warning that server-side eval is disabled
- The cron job in `/etc/cron.d/craft-dashboard` that runs `docker compose exec -T app python scripts/run_llm.py evaluate` can remain enabled but will be a no-op
- Evaluation only happens via the pull-based client

When `ENABLE_SERVER_EVAL=true` (default, backward compatible):
- Server-side evaluation via OpenRouter works as before
- The pull-based client also works (both can coexist)

### 5. Remove `LocalLLMClient` from Server

The `LocalLLMClient` class in `craft_dashboard/llm/client.py` is removed from the
server deployment. The `LLM_BACKEND=local` setting is removed. The client script
owns the local LLM connection.

The `create_llm_client()` factory in settings.py only creates `OpenRouterClient`.
The `LocalLLMClient` moves to a shared module that the client script imports.

## Implementation Steps

- [ ] **Phase 1: Server API**
  - [ ] Create `/api/eval/` router with `next`, `result`, and `status` endpoints
  - [ ] Add `EVAL_API_TOKEN` setting and bearer token auth middleware
  - [ ] Add issue locking mechanism (DB column `eval_locked_until` on evaluations table)
  - [ ] Add validation on result submission (reuse existing validation)
  - [ ] Write tests for all new endpoints

- [ ] **Phase 2: Local Client**
  - [ ] Create `scripts/eval_client.py` with polling loop
  - [ ] Reuse evaluator, prompts, and validation from existing code
  - [ ] Add CLI options mirroring `run_llm evaluate` controls
  - [ ] Add progress logging, retry logic, graceful shutdown
  - [ ] Write tests for client behavior

- [ ] **Phase 3: Server Toggle**
  - [ ] Add `ENABLE_SERVER_EVAL` setting (default: true)
  - [ ] Gate `run_llm evaluate` behind the toggle
  - [ ] Update admin UI to show toggle status
  - [ ] Update deployment docs

- [ ] **Phase 4: Remove Local LLM from Server**
  - [ ] Move `LocalLLMClient` out of server code path
  - [ ] Remove `LLM_BACKEND=local` option from settings
  - [ ] Remove `LOCAL_LLM_*` settings from server
  - [ ] Update docs and deployment templates

- [ ] **Phase 5: Documentation**
  - [ ] Update `docs/architecture.md` with new eval flow
  - [ ] Add `docs/eval-client.md` with setup instructions
  - [ ] Update `docs/deployment.md` with new env vars

## Migration Path

1. Deploy Phase 1 (API endpoints) — no behavior change (`docker compose pull && docker compose up -d`)
2. Set up eval client on local machine, verify it works against the server
3. Set `ENABLE_SERVER_EVAL=false` in `.env` on the VPS and redeploy
4. Deploy Phase 4 (remove local LLM code from server image)
5. Server only uses OpenRouter (if enabled) or relies entirely on pull-based client

## Security Considerations

- The eval API token should be long and random (≥32 chars)
- Rate limit the `/api/eval/next` endpoint to prevent abuse
- The content hash check on result submission prevents replay attacks
- The lock TTL prevents stale locks from blocking evaluation
- No network path from VPS to developer machine exists in the new architecture

