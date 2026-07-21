"""Coordinated rate-limit backoff shared across concurrent evaluation workers.

The LLM HTTP clients (see ``craft_dashboard.llm.client``) already retry
individual 429 responses with their own per-request backoff, and the
orchestrator's own retry loop (``_evaluate_issue_with_retries``) adds a
second layer on top of that. Both of those are blind to what *other*
concurrent workers are doing: under ``--concurrency > 1``, several workers
can each independently retry straight back into the same rate limit right
after another worker's backoff clears, wasting requests and quota.

``SharedRateLimiter`` adds a single coordinated cooldown window: whenever
*any* worker observes a 429, every worker pauses new requests until the
cooldown clears, rather than each retrying on its own schedule.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

DEFAULT_MIN_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 60.0


class SharedRateLimiter:
    """Coordinates a shared rate-limit cooldown window across workers.

    Safe to share across concurrently-running asyncio tasks: all state
    mutation happens under a single ``asyncio.Lock``.
    """

    def __init__(
        self,
        *,
        min_backoff: float = DEFAULT_MIN_BACKOFF_SECONDS,
        max_backoff: float = DEFAULT_MAX_BACKOFF_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Initialize the limiter.

        Args:
            min_backoff: Cooldown applied after the first consecutive 429,
                in seconds (used when the response has no ``Retry-After``).
            max_backoff: Upper bound on the exponentially-growing cooldown.
            sleep: Sleep function to await in :meth:`wait_if_throttled`
                (overridable in tests to avoid real delays).

        """
        self._lock = asyncio.Lock()
        self._resume_at = 0.0
        self._consecutive_hits = 0
        self._min_backoff = min_backoff
        self._max_backoff = max_backoff
        self._sleep = sleep

    async def wait_if_throttled(self) -> None:
        """Sleep until any currently in-effect shared cooldown has cleared."""
        async with self._lock:
            delay = self._resume_at - time.monotonic()
        if delay > 0:
            await self._sleep(delay)

    async def report_rate_limited(self, *, retry_after: float | None = None) -> None:
        """Record a 429 and extend the shared cooldown window.

        Args:
            retry_after: Seconds to wait, taken from the response's
                ``Retry-After`` header when present. Falls back to an
                exponential backoff (doubling per consecutive hit, capped at
                ``max_backoff``) when the server doesn't specify one.

        """
        async with self._lock:
            self._consecutive_hits += 1
            backoff = (
                retry_after
                if retry_after is not None
                else min(
                    self._min_backoff * (2 ** (self._consecutive_hits - 1)),
                    self._max_backoff,
                )
            )
            self._resume_at = max(self._resume_at, time.monotonic() + backoff)

    async def report_success(self) -> None:
        """Reset the consecutive-hit counter after a request succeeds."""
        async with self._lock:
            self._consecutive_hits = 0


def parse_retry_after(value: str | None) -> float | None:
    """Parse an HTTP ``Retry-After`` header value in delay-seconds form.

    Returns None for missing/HTTP-date-formatted values (the latter is rare
    for LLM APIs in practice and not worth the extra parsing complexity
    here); callers should fall back to exponential backoff in that case.
    """
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
