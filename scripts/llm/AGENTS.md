# scripts/llm/

Operator entry points for the evaluation pipeline. These run against
production data and spend money.

## Contract

- `eval_worker.py` — the long-running pull-based worker. Claims work from
  `/api/eval/next`, evaluates, posts results back. Never writes to git
  mirrors.
- `canary.py` — evaluates a small explicit set of issues one at a time against
  the real pipeline, for validating a prompt or model change before a full
  rollout.

## Invariants

- Every script takes `--dry-run` and honours it end to end.
- Log the model, the token counts, and the cost of each call. An unexplained
  spend spike must be traceable to a run.
- Retry policy comes from `craft_dashboard.http_retry`.
- A worker must be able to die at any point without corrupting state: claims
  expire, they are not held in memory.

## Tests

```bash
uv run pytest tests/unit/scripts -q
```
