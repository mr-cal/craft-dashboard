"""In-memory liveness signals reported by the evaluation worker.

The worker's health is inferred from activity on the ``/api/eval`` endpoints
themselves rather than from a separate heartbeat, so it works identically
whether the worker runs in-cluster or from a developer's laptop.

This state lives here, not in the route module, so ``AdminService`` can read it
without a service importing a route.

The app runs as a single gunicorn worker process (see Dockerfile), so
module-level state is sufficient — no heartbeat table or database writes.
State resets on restart, and callers must treat "unknown" as distinct from
"idle".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: How recently ``/next``/``/result`` must have been called for the service to
#: be considered "running" rather than "stalled". A bit more than 2x the
#: default 30s poll interval, to tolerate a slow evaluation or a missed tick
#: without flapping.
ACTIVITY_STALE_AFTER = timedelta(seconds=90)

#: How long evaluation pauses for when the daily spend cap trips.
AUTO_QUOTA_PAUSE_FOR = timedelta(minutes=30)

#: Minimum gap between recorded queue-depth snapshots. Sampled as a side
#: effect of ``/next`` (called by every worker every poll interval), throttled
#: in-memory so frequent pollers don't turn this into an extra query per poll.
QUEUE_SNAPSHOT_INTERVAL = timedelta(minutes=5)


@dataclass
class EvalActivityState:
    """Mutable worker-liveness state for the current process."""

    last_next_call_at: datetime | None = None
    last_result_submitted_at: datetime | None = None
    last_queue_snapshot_at: datetime | None = None
    quota_paused_until: datetime | None = None


#: The single per-process instance. Tests mutate its fields directly.
state = EvalActivityState()


def record_next_call() -> None:
    """Note that a worker polled ``/next``."""
    state.last_next_call_at = datetime.now(tz=UTC)


def record_result_submitted() -> None:
    """Note that a worker submitted an evaluation result."""
    state.last_result_submitted_at = datetime.now(tz=UTC)


def get_eval_activity() -> tuple[datetime | None, datetime | None]:
    """Return (last ``/next`` call time, last ``/result`` submission time)."""
    return state.last_next_call_at, state.last_result_submitted_at


def get_quota_pause_until() -> datetime | None:
    """Return when the worker reported it would resume after a quota pause.

    Returns ``None`` once that time has passed, so a stale report from hours
    ago can't linger and misreport a since-recovered worker as paused.
    """
    paused_until = state.quota_paused_until
    if paused_until is not None and datetime.now(tz=UTC) >= paused_until:
        return None
    return paused_until


def set_quota_pause_until(resume_at: datetime | None) -> None:
    """Record when evaluation should resume after a quota pause."""
    state.quota_paused_until = resume_at


def pause_for_spend_cap() -> None:
    """Pause evaluation because today's spend exceeded the configured cap."""
    state.quota_paused_until = datetime.now(tz=UTC) + AUTO_QUOTA_PAUSE_FOR


def should_record_queue_snapshot() -> bool:
    """Return whether enough time has passed to record another snapshot.

    Advances the throttle as a side effect when it returns ``True``.
    """
    now = datetime.now(tz=UTC)
    last = state.last_queue_snapshot_at
    if last is not None and now - last < QUEUE_SNAPSHOT_INTERVAL:
        return False
    state.last_queue_snapshot_at = now
    return True


def reset() -> None:
    """Clear all recorded activity. Intended for tests."""
    state.last_next_call_at = None
    state.last_result_submitted_at = None
    state.last_queue_snapshot_at = None
    state.quota_paused_until = None
