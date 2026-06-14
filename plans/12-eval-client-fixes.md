# Eval Client Fixes — Infinite Loop & Progress Bar

## Bugs Observed

1. **Same issue evaluated repeatedly with `--force`**: The client polls `/api/eval/next`, evaluates, submits via `/api/eval/result`, then immediately gets the same issue again.
2. **Progress bar shows wrong total with `--force`**: Status endpoint returns `pending=0` (only counts unevaluated issues), but `--force` should re-evaluate all open issues. Progress bar shows 0/0 or similar.

---

## Root Cause Analysis

### Bug 1: No lock on submitted evaluation

`/api/eval/result` (eval_api.py:263) creates the new `LLMEvaluation` with `eval_locked_until=None`. On the next poll:
- The lock check `eval_locked_until.is_(None)` matches → issue is unlocked
- `--force=True` bypasses the content-hash skip check
- Query orders by `Issue.id` → same issue returned first

**Fix**: Set `eval_locked_until = datetime.now(tz=UTC) + _LOCK_TTL` on the newly created evaluation.

### Bug 2: Status endpoint ignores filter parameters

`/api/eval/status` always counts `pending` as "open issues with no evaluation". It doesn't accept the same filter params (`force`, `incomplete`, `stale_days`, `project`) that `/api/eval/next` uses. With `--force`, all open issues are work items, not just unevaluated ones.

**Fix**: Accept filter parameters on the status endpoint so it returns accurate pending counts for the current mode.

---

## Proposed Changes

### 1. Fix lock on submitted evaluation (eval_api.py)

**File**: `craft_dashboard/routes/eval_api.py`, line ~278

Change the `submit_result` endpoint to set `eval_locked_until` on the new evaluation:

```python
session.add(
    LLMEvaluation(
        ...
        eval_locked_until=None,  # ← remove or keep None
    )
)
```

becomes:

```python
session.add(
    LLMEvaluation(
        ...
        eval_locked_until=datetime.now(tz=UTC) + _LOCK_TTL,
    )
)
```

This ensures the issue is locked for 10 minutes after evaluation, preventing it from being returned again immediately.

### 2. Status endpoint accepts filter params (eval_api.py)

Add query parameters to the status endpoint matching the next endpoint:

```python
@router.get("/status")
async def eval_status(
    request: Request,
    *,
    authorization: str = Header(default=""),
    project: str = Query(default=""),
    open_only: bool = Query(default=True),
    force: bool = Query(default=False),
    incomplete: bool = Query(default=False),
    stale_days: int = Query(default=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
```

And compute `pending` using the same logic as `_fetch_issue_and_latest_evaluation`:
- When `force=True`: `pending = total_open - total_evaluated` (or count issues where the skip condition would fail)
- When `incomplete=True`: count issues with missing summary/scores
- When `stale_days > 0`: count issues with evaluations older than N days
- Normal mode: existing logic (unevaluated issues only)

### 3. Client sends filter params to status endpoint (eval_client.py)

The client currently calls status without filter params:

```python
status_resp = await http_client.get("/api/eval/status", headers=headers)
```

Should include the same params:

```python
status_resp = await http_client.get(
    "/api/eval/status",
    params=params,
    headers=headers,
)
```

### 4. Progress bar total logic (eval_client.py)

Update the progress bar total calculation to account for `--force`:

```python
if force:
    task_total = min(total_open, limit) if limit > 0 else total_open
elif incomplete:
    task_total = min(server_pending, limit) if limit > 0 else server_pending
else:
    # stale_days mode
    ...
```

---

## Design Decisions

### Why not change the lock behavior differently?

Setting `eval_locked_until` on the new evaluation is the simplest and most correct fix. It matches the pattern already used when claiming an issue via the `next` endpoint. The 10-minute lock is generous enough that a long evaluation won't cause a collision, but short enough that the issue becomes available again if the client crashes.

### Why add filter params to status instead of a separate endpoint?

Keeps the API simpler — one status call, same semantics across endpoints. The client already computes these params; it just needs to pass them along.

### What about `--open-only` default?

The current `--open-only/--all-issues` flag defaults to `open_only=True`, which is sensible for normal use. No change needed here, but the flag could be more clearly named. Consider `--scope open|all` in a future refactor. Not part of this fix.

---

## Verification

1. Run `uv run scripts/eval_client.py evaluate --open-only --force --limit 3` — should evaluate 3 different issues, not the same one 3 times
2. Progress bar should show total = number of open issues when `--force` is used
3. Without `--force`, existing behavior should be unchanged (only unevaluated issues shown)
4. `make lint` and `make test` succeed
