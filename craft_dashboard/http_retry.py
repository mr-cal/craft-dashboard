"""Shared HTTP retry policy for outbound API calls.

Every outbound integration (Discourse forums, OpenRouter, local LLM
endpoints) talks to a rate-limited HTTP service and needs the same three
behaviours: retry transient failures, respect ``Retry-After`` when the
server sends it, and log each retry. Keeping one implementation here means a
fix to the retry semantics applies everywhere instead of to whichever copy
was edited.

Callers supply their own attempt counts and backoff curves, since a forum
scrape and a 10-minute LLM completion tolerate very different waits.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    from tenacity import RetryCallState

__all__ = [
    "is_retriable_http_error",
    "make_before_sleep_log",
    "make_wait_honoring_retry_after",
    "retry_after_seconds",
]

HTTP_TOO_MANY_REQUESTS = 429


def is_retriable_http_error(exc: BaseException) -> bool:
    """Return whether an exception represents a transient HTTP failure.

    Retries 429 (rate limited) and transport-level errors.
    ``httpx.TransportError`` covers timeouts, network errors, and protocol
    errors such as the server dropping the connection mid-response.

    Deliberately does not retry other 4xx responses: a 400 or 403 is a
    client-side problem that repeating will not fix, and for paid APIs a
    402 means retrying costs money without any chance of succeeding.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == HTTP_TOO_MANY_REQUESTS
    return isinstance(exc, httpx.TransportError)


def retry_after_seconds(exc: BaseException) -> float | None:
    """Return the ``Retry-After`` delay from a response, if it carries one.

    Only the delta-seconds form is handled; the HTTP-date form is rare in
    the APIs this app calls, and falling back to exponential backoff for it
    is safe.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    retry_after = exc.response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        return float(retry_after)
    except ValueError:
        return None


def make_wait_honoring_retry_after(
    default_wait: Callable[[RetryCallState], float],
) -> Callable[[RetryCallState], float]:
    """Wrap a tenacity wait strategy so ``Retry-After`` takes precedence.

    A server that sends ``Retry-After`` is stating exactly how long to wait.
    Backing off for less gets the next request rejected too; backing off for
    much longer wastes the run's time budget.

    Falls back to ``default_wait`` when no header is present, such as for
    transport errors or a 429 that omitted it.
    """

    def _wait(retry_state: RetryCallState) -> float:
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        if exception is not None:
            retry_after = retry_after_seconds(exception)
            if retry_after is not None:
                return retry_after
        return default_wait(retry_state)

    return _wait


def make_before_sleep_log(
    logger: logging.Logger,
    max_attempts: int,
    *,
    level: int = logging.WARNING,
    prefix: str = "HTTP retry",
) -> Callable[[RetryCallState], None]:
    """Build a tenacity ``before_sleep`` hook that logs each retry.

    ``before_sleep`` fires ahead of every retry, once the previous attempt's
    failure is known, unlike ``before``, which tenacity calls only once. A
    retried attempt can still incur real cost (a dropped connection after a
    model has begun generating, for example), so these are worth recording.
    """

    def _before_sleep(retry_state: RetryCallState) -> None:
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        if exception is None:
            return
        exc_name = type(exception).__name__
        exc_msg = str(exception).strip()
        detail = f"{exc_name}: {exc_msg}" if exc_msg else exc_name
        logger.log(
            level,
            "%s (attempt %d/%d): %s",
            prefix,
            retry_state.attempt_number,
            max_attempts,
            detail,
        )

    return _before_sleep
