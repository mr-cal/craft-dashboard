# llm/

Model clients, prompts, embeddings, evaluation, and duplicate detection.
Every call here costs money.

## Contract

- May import: `models`, `repositories`, `http_retry`, `settings`, `git_mirrors`.
- Must not import: `routes`.
- Retry and backoff come from `craft_dashboard.http_retry`. Do not write a
  local `_is_retriable`.

## Invariants

- **Bound every retry loop.** Retrying a 400 that happens to mention "token"
  bills for each attempt. Match on the provider's structured error code, not a
  substring, and cap the depth.
- **Do not retry 402.** The budget is gone; retrying bills without succeeding.
- **Count what actually happened.** Reporting `candidates_compared` as
  `len(candidates)` before making any call makes a total provider outage look
  like "no duplicates found". Track failures separately.
- Worker liveness state belongs in `services/eval_activity.py`, not in a route
  module.

## Tests

```bash
uv run pytest tests/unit/llm -q
```
