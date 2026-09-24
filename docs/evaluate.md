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
- Runs 10 concurrent worker coroutines by default (`--concurrency`). **The
  production VPS deployment pins this to `--concurrency 6`** once the Phase 6
  tool-calling loop is live: each concurrent evaluation now also runs git
  subprocesses against the local mirrors (`git grep`, `git log`, `git show`),
  and the VPS has only ~307MB RAM available alongside postgres, caddy, and the
  app itself. A separate semaphore (1-2 permits) bounds simultaneous git
  subprocesses independently of eval concurrency, and the VPS sets
  `git grep --threads=1` to cap per-process RAM.
- Uses `--llm-backend openrouter|local` for evaluation text generation.
- All embeddings are computed 100% server-side on submission; local evaluation workers do not require an embedding key or compute embeddings.
- There is no separate `embed` command anymore.

## Prerequisites

- Python 3.12+
- A clone of the `craft-dashboard` repository
- `DASHBOARD_URL` and `EVAL_API_TOKEN` to connect to the craft-dashboard server
- `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` (or split `LLM_MODEL_SUMMARY` / `LLM_MODEL_SCORING`)

## Setup

Copy `.env.example` to `.env` and fill in the worker settings in Section 3
of the file. The command loads `.env` automatically.

### TLS certificates

There are two separate TLS cert settings, configured via environment variables in `.env`:

| Variable | Purpose |
|---|---|
| `DASHBOARD_CA_CERT` | CA cert to verify the **craft-dashboard server** |
| `LLM_CA_CERT` | CA cert to verify the **LLM endpoint** |

## Usage

Run the worker from the repository root:

```bash
# Continuous evaluation using configured LLM endpoint
uv run scripts/run_llm.py evaluate

# Continuous evaluation with higher concurrency
uv run scripts/run_llm.py evaluate \
  --project snapcraft \
  --concurrency 4

# Continuous evaluation with Copilot CLI and Gemini 3.8 Flash
COPILOT_ACP_MODEL=gemini-3.8-flash uv run scripts/run_llm.py evaluate \
  --llm-backend copilot-acp

# Bounded run for one project
uv run scripts/run_llm.py evaluate --limit 10
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

### Paced evaluations (rate-limit safe)

To run evaluations slowly with randomized intervals (e.g. to emulate human developer pacing or stay within strict provider burst rate limits), use the `--slow-eval` flag on `run_llm.py evaluate`:

```bash
# Evaluate with --slow-eval (forces concurrency=1, 25-55s jitter between issues, and 7s pacing between tool rounds)
uv run scripts/run_llm.py evaluate --slow-eval --limit 10

# Customize jitter and intra-issue tool delay
uv run scripts/run_llm.py evaluate --slow-eval --min-delay 30 --max-delay 60 --tool-delay 10 --limit 10
```

## Architecture note

Evaluation is pull-based:

- `GET /api/eval/next` returns the next issue to evaluate,
- `POST /api/eval/result` stores the finished evaluation; the server computes the
  summary and search embeddings via OpenRouter,
- `GET /api/eval/status` reports queue progress,
- the worker initiates every connection over HTTPS,
- the server never opens an outbound connection to your machine or local LLM.

This keeps local and home-lab setups simple: you can point the worker at the
public craft-dashboard API without exposing your developer machine.

## Canary rollout

Bumping one of the evaluation version constants (such as `OPEN_ISSUE_EVAL_VERSION`
or `OPEN_PR_EVAL_VERSION` in `craft_dashboard/llm/evaluator.py`) makes
every currently-`latest` evaluation for that state/type "outdated" and the
continuous worker's `/api/eval/next` polling naturally re-surfaces all of
them. To avoid a bug in a new evaluator/prompt version silently wasting time and
money re-evaluating the entire backlog before anyone notices, use
`scripts/llm/canary.py` to hand-pick a small number of real issues and
evaluate them one at a time, **before** touching the version constants:

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
on or requires an evaluation version bump) and persists its result to the
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
2. **Evaluation version bump.** Only after the canary is reviewed and
   approved, bump the target evaluation version constant (e.g. `OPEN_ISSUE_EVAL_VERSION`).
   This re-queues items in that category, but nothing evaluates yet until the worker
   actually claims work.
3. **Safety-margin batch (50 issues).** Run `evaluate --limit 50` (see
   `--limit`/`--max-evaluations` above) and review a sample of the results
   and the admin page's cost/error dashboard before continuing.
4. **Full backfill.** Let the continuous worker (pinned to
   `--concurrency 6` in production) drain the remaining backlog at its
   normal polling cadence, checking the cost dashboard periodically.

If any stage surfaces a problem, stop and roll back rather than proceeding to the next stage.

## Evaluation versioning & granular bumps

Evaluations are versioned by four granular constants in `craft_dashboard/llm/evaluator.py`:

- `OPEN_ISSUE_EVAL_VERSION` (~2,000 items): open issues scoring prompt.
- `OPEN_PR_EVAL_VERSION` (~250 items): open PRs scoring prompt.
- `CLOSED_ISSUE_EVAL_VERSION` (~15,000 items): closed issues resolution/summary prompt.
- `CLOSED_PR_EVAL_VERSION` (~2,000 items): closed/merged PRs resolution/summary prompt.

Bumping any of these constants makes only that specific category's latest evaluations
"outdated" — the next `/api/eval/next` poll will naturally re-surface them via
`build_pending_evaluation_query()`. No manual database write is required to mark rows stale.

Running the worker unthrottled against large backlogs risks exhausting the day's
OpenRouter quota in one run and produces an unreviewable wall of data all at once. Roll it out in stages instead:

1. **Smallest project first.** Run with `--project <smallest-project>
   --limit 20` and manually review a sample of the resulting evaluations
   (via the issue detail page's evaluation provenance panel) before
   proceeding further.
2. **One project at a time, capped.** For each remaining project, run
   `--project <name> --limit 100` per invocation, checking the admin
   page's queue depth and quota-pause status between runs before starting
   the next project.
3. **Full backlog, capped rate.** Once staged runs look correct, run the
   continuous worker (`--concurrency 6`, no `--project` filter) and let it
   drain the full ~2,269-item backlog over its normal polling cadence —
   do not raise `--concurrency` above the VPS-pinned value of 6 (Task 11)
   to "speed up" the backfill; this is a one-time cost that trades wall
   time for RAM/API-quota safety.
4. **Monitor cost.** Each evaluation's `cost_usd` is stored per-row (Phase
   1); check the admin page's cost dashboard partway through the backfill
   to catch a runaway cost trend (e.g. an unexpectedly expensive tool-call
   loop) before it consumes the full backlog's budget. The daily spend cap
   (`EVAL_DAILY_SPEND_CAP_USD`) auto-pauses evaluation if the day's total
   exceeds the configured limit.

If a backfill run must be aborted partway through, it is always safe to
resume later with the same flags — `/api/eval/next`'s `latest=False` flip
convention means no partial evaluation is ever left half-applied, and
re-running the same command simply continues from wherever the queue
naturally resumes.

## Rolling back a version bump

If a version rollout produces bad evaluations, roll back **before** letting the
backfill drain further:

1. `git revert` the version bump commit and redeploy, so the
   constant returns to its previous value and the queue stops treating older rows as stale.
2. For any issue that already got a v5 `latest` row, restore the prior v4
   row as current (this is the exact drill exercised in Task 12, Step 2):

   ```sql
   -- Demote the v5 row, promote the previously-current v4 row.
   UPDATE llm_evaluations SET latest = false WHERE id = <v5_row_id>;
   UPDATE llm_evaluations SET latest = true  WHERE id = <prev_v4_row_id>;
   ```

   (The v5 rows can be left in place with `latest=false` for audit, or
   deleted; they are hidden from all views once demoted.)

Rollback was proven reversible on a sample before the backfill started
(Task 12, Step 2) — this section is the copy-paste of that proven procedure,
not an untested improvisation.
