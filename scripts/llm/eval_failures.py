"""Classification and logging of errors raised while evaluating one issue.

``_evaluate_issue`` catches everything an evaluation can raise and has to
decide two things: what release reason to record, and whether the failure is a
quota exhaustion (which pauses every worker) or an ordinary failure (which
trips the circuit breaker). That decision, and its log line, lives here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from craft_dashboard.llm.evaluator import EvaluationDiscarded
from craft_dashboard.llm.exceptions import (
    LLMQuotaError,
    LLMTimeoutError,
    LLMUnavailableError,
)

from scripts.llm.console import format_elapsed
from scripts.llm.eval_http import format_error_body

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationFailure:
    """How the worker should react to a failed evaluation attempt."""

    release_reason: str
    is_quota: bool = False


def classify_evaluation_error(
    exc: Exception, *, issue_ref: str, elapsed: float
) -> EvaluationFailure:
    """Log ``exc`` in its most specific form and return the reaction to take.

    Must be called from inside the ``except`` block that caught ``exc`` so the
    debug tracebacks pick up the active exception.
    """
    if isinstance(exc, EvaluationDiscarded):
        logger.warning(
            "%s: evaluation discarded (post-preflight tool failure): %s; "
            "releasing claim, submitting nothing",
            issue_ref,
            exc,
        )
        return EvaluationFailure(release_reason="evaluation_discarded")

    if isinstance(exc, LLMQuotaError):
        return EvaluationFailure(release_reason="quota_exhausted", is_quota=True)

    if isinstance(exc, httpx.TimeoutException | LLMTimeoutError):
        logger.error(
            "%s: evaluation failed after %s: LLM request timed out (%s)",
            issue_ref,
            format_elapsed(elapsed),
            type(exc).__name__,
        )
        logger.debug("%s: LLM timeout traceback:", issue_ref, exc_info=True)
    elif isinstance(exc, LLMUnavailableError):
        logger.error(
            "%s: evaluation failed after %s: LLM service unavailable (%s)",
            issue_ref,
            format_elapsed(elapsed),
            exc,
        )
        logger.debug("%s: LLM unavailable traceback:", issue_ref, exc_info=True)
    elif isinstance(exc, httpx.HTTPStatusError):
        logger.error(
            "%s: evaluation failed after %s: HTTP %d from %s — %s",
            issue_ref,
            format_elapsed(elapsed),
            exc.response.status_code,
            exc.response.url,
            format_error_body(exc.response),
        )
        logger.debug("%s: HTTP error traceback:", issue_ref, exc_info=True)
    else:
        logger.error(
            "%s: evaluation failed after %s: %s: %s",
            issue_ref,
            format_elapsed(elapsed),
            type(exc).__name__,
            exc,
        )
        logger.debug("%s: evaluation traceback:", issue_ref, exc_info=True)

    return EvaluationFailure(release_reason="evaluation_error")
