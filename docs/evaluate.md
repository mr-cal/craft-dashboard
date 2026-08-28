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

## Phase 5 bake-off hard gate

Phase 5's standalone bake-off tooling is a hard gate before any Phase 6
deep-evaluation backfill. A human must review and sign off on:

- `report_scoring.md`
- `report_summary.md`
- `report_grading.md`

The Phase 6 backfill must not start until those three reports have been
generated, reviewed, and explicitly approved.

See [`docs/how-to.md`](how-to.md) for the operator workflow that produces the
reports.

## Canary rollout

Bumping `CURRENT_EVAL_VERSION` (see `craft_dashboard/llm/evaluator.py`) makes
every currently-`latest` open issue/PR evaluation "outdated" and the
continuous worker's `/api/eval/next` polling naturally re-surfaces all of
them — there is no way to make a version bump affect only a handful of items.
To avoid a bug in a new evaluator/prompt version silently wasting time and
money re-evaluating the entire backlog before anyone notices, use
`scripts/llm/canary.py` to hand-pick a small number of real issues and
evaluate them one at a time, **before** touching `CURRENT_EVAL_VERSION`:

```bash
uv run scripts/llm/canary.py \
  --server https://craft-dashboard.name \
  --token "$EVAL_API_TOKEN" \
  --issue snapcraft:6381 \
  --issue debcraft:41 \
  --issue "snapcraft (launchpad):1861614" \
  --issue snapcraft-rocks:111 \
  --issue craft-parts:766
```

Each `--issue PROJECT:NUMBER` target is evaluated with `--force`/`--issue`
semantics (bypassing version/hash eligibility entirely — this never depends
on or requires a `CURRENT_EVAL_VERSION` bump) and persists its result to the
live database exactly like a normal evaluation, so the resulting
`suggested_action`/`suggested_action_reason`/`impact`/`related_work` can be
reviewed on the real issue detail page. Each target also gets its own hard
`--timeout-seconds` (default 300s); the batch stops at the first timeout or
error instead of continuing through the rest of the list, so a hang or bug
affects at most one issue.

Full staged rollout, from smallest to largest blast radius:

1. **Canary (5 issues).** Run `canary.py` against 5 hand-picked real issues
   (as above). A human reviews the resulting evaluations on the live issue
   detail pages before proceeding.
2. **`CURRENT_EVAL_VERSION` bump.** Only after the canary is reviewed and
   approved, bump `CURRENT_EVAL_VERSION` (see
   `plans/42-deep-evaluation-phase6-prompt-rewrite-and-backfill.md`'s Task 12
   runbook for the exact gate/rollback procedure). This re-queues the full
   ~2,269-item backlog, but nothing evaluates yet until the worker actually
   claims work.
3. **Safety-margin batch (50 issues).** Run `evaluate --limit 50` (see
   `--limit`/`--max-evaluations` above) and review a sample of the results
   and the admin page's cost/error dashboard before continuing.
4. **Full backfill.** Let the continuous worker (pinned to
   `--concurrency 6` in production) drain the remaining backlog at its
   normal polling cadence, checking the cost dashboard periodically.

If any stage surfaces a problem, stop and roll back (see Task 12's rollback
procedure) rather than proceeding to the next stage.
