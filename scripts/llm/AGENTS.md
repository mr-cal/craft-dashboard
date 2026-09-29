# scripts/llm/

Operator entry points for the evaluation pipeline. These run against
production data and spend money.

## Contract

- `eval_worker.py` — the long-running pull-based worker. Claims work from
  `/api/eval/next`, evaluates, posts results back. Never writes to git
  mirrors. This is the entry point; the modules below are its internals and
  must not be imported by anything outside `scripts/llm/`.
  - `worker_runtime.py` — process-wide pause/shutdown flags, per-run
    counters (`RunState`), the dependency bundle (`Runtime`), and the
    signal/TTY plumbing.
  - `eval_http.py` — one wrapper per `/api/eval/*` endpoint. No policy.
  - `eval_payload.py` — pure claim/result parsing and `/result` payload
    building. No I/O.
  - `eval_failures.py` — classifies an exception raised during evaluation
    into a release reason plus "is this a quota exhaustion?".
  - `eval_startup.py` — backend client construction and one-time setup.
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
