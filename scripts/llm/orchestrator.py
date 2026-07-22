"""Orchestration for running LLM evaluations over issues."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict

import httpx
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.llm.exceptions import (
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMValidationError,
)
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from scripts.eval_timing import PHASE_EVALUATE, TimingHistory
from scripts.llm.checkpoint import (
    EvaluationCheckpoint,
    clear_checkpoint,
    compute_filter_hash,
    load_checkpoint,
    save_checkpoint,
)
from scripts.llm.console import ProgressTracker, make_progress
from scripts.llm.pricing import estimate_cost_usd, format_usd
from scripts.llm.queries import (
    IssueFilter,
    count_issue_evaluation_targets,
    fetch_issue_evaluation_breakdown,
    stream_issue_evaluation_targets,
)
from scripts.llm.ratelimit import SharedRateLimiter, parse_retry_after
from scripts.llm.storage import store_evaluation_result
from scripts.llm.validation import validate_evaluation_result

if TYPE_CHECKING:
    from craft_dashboard.llm.evaluator import EvaluationResult, IssueEvaluator
    from rich.console import Console
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from scripts.llm.queries import IssueEvaluationTarget


class EvaluationStats(TypedDict):
    """Progress counters returned by the LLM orchestration loop."""

    evaluated: int
    skipped: int
    errored: int
    total_tokens: int
    total_prompt_tokens: int
    total_completion_tokens: int
    estimated_cost_usd: float
    unpriced_evaluations: int


class DryRunEvaluationStats(EvaluationStats):
    """Evaluation counters returned when running in dry-run mode."""

    would_evaluate: int


logger = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})
_HTTP_TOO_MANY_REQUESTS = 429
_retry_sleep = asyncio.sleep


def _is_retryable_evaluation_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return isinstance(exc, (httpx.TimeoutException, LLMTimeoutError, LLMRateLimitError))


async def _log_retry_attempt(
    retry_state: RetryCallState,
    *,
    issue_ref: str,
    rate_limiter: SharedRateLimiter,
) -> None:
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    if exception is None:
        return
    logger.warning(
        "Retrying %s after attempt %d/%d in %.1fs: %s",
        issue_ref,
        retry_state.attempt_number,
        3,
        retry_state.next_action.sleep,
        exception,
    )
    if (
        isinstance(exception, httpx.HTTPStatusError)
        and exception.response.status_code == _HTTP_TOO_MANY_REQUESTS
    ):
        # Coordinate across all concurrent workers, not just this one: a 429
        # here means the whole run should back off, not just this request's
        # own retry loop.
        retry_after = parse_retry_after(exception.response.headers.get("Retry-After"))
        await rate_limiter.report_rate_limited(retry_after=retry_after)


async def _evaluate_issue_with_retries(
    evaluator: IssueEvaluator,
    issue_kwargs: dict[str, object],
    issue_ref: str,
    rate_limiter: SharedRateLimiter,
) -> EvaluationResult | None:
    async def _before_sleep(retry_state: RetryCallState) -> None:
        await _log_retry_attempt(
            retry_state, issue_ref=issue_ref, rate_limiter=rate_limiter
        )

    async for attempt in AsyncRetrying(
        retry=retry_if_exception(_is_retryable_evaluation_error),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        stop=stop_after_attempt(3),
        before_sleep=_before_sleep,
        sleep=_retry_sleep,
        reraise=True,
    ):
        with attempt:
            await rate_limiter.wait_if_throttled()
            result = await evaluator.evaluate(**issue_kwargs)
            await rate_limiter.report_success()
            return result
    msg = f"Retry loop exited unexpectedly for {issue_ref}"
    raise RuntimeError(msg)


def _log_progress(
    stats: EvaluationStats, *, processed: int, total_to_eval: int
) -> None:
    if processed % 10 == 0:
        pct = (processed / total_to_eval * 100) if total_to_eval > 0 else 0
        cost_suffix = (
            f", ~{format_usd(stats['estimated_cost_usd'])} spent"
            if stats["estimated_cost_usd"] > 0
            else ""
        )
        logger.info(
            "Progress [%d/%d] (%.0f%%): %d evaluated, %d skipped, %d errors%s",
            processed,
            total_to_eval,
            pct,
            stats["evaluated"],
            stats["skipped"],
            stats["errored"],
            cost_suffix,
        )


def _build_issue_kwargs(
    target: IssueEvaluationTarget, maintainers: set[str], *, force: bool
) -> dict[str, object]:
    """Build the evaluator prompt kwargs for one issue evaluation target."""
    issue = target.issue
    existing_hash = (
        None
        if force
        else (
            target.issue_data_hash
            if target.eval_version == CURRENT_EVAL_VERSION
            else None
        )
    )
    labels = issue.labels if isinstance(issue.labels, list) else []

    now = datetime.now(tz=UTC)
    created = issue.created_at or now
    updated = issue.updated_at or now
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)

    age_days = (now - created).days
    last_activity_days = (now - updated).days
    issue_comments = issue.comments if issue.comments else []
    pr_details = (
        issue.metadata_
        if issue.issue_type == "pull_request" and issue.metadata_
        else None
    )
    # Closed (non-PR) issues may have closing PRs recorded by the collector
    # in metadata_; surface them so build_closed_evaluate_prompt() can
    # reference what actually resolved the issue.
    closing_references = (
        (issue.metadata_ or {}).get("closing_references")
        if issue.issue_type != "pull_request"
        else None
    )

    return {
        "title": issue.title,
        "body": issue.body,
        "issue_type": issue.issue_type,
        "state": issue.state,
        "labels": labels,
        "age_days": age_days,
        "last_activity_days": last_activity_days,
        "author": issue.author or "unknown",
        "is_maintainer": issue.author in maintainers if issue.author else False,
        "comment_count": len(issue_comments),
        "comments": issue_comments,
        "pr_details": pr_details,
        "closing_references": closing_references,
        "existing_hash": existing_hash,
    }


async def _evaluate_issues(
    session_factory: async_sessionmaker[AsyncSession],
    evaluator: IssueEvaluator,
    maintainers: set[str],
    project_filter: str = "",
    limit: int = 0,
    open_only: bool = False,
    force: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
    dry_run: bool = False,
    llm_backend: str = "openrouter",
    strict_validation: bool = False,
    resume: bool = True,
    concurrency: int = 1,
    console: Console | None = None,
) -> EvaluationStats | DryRunEvaluationStats:
    """Evaluate matched issues and persist any new results.

    ``concurrency`` controls how many issues are evaluated at once (workers
    pull from a shared queue of targets). A value of 1 preserves the original
    strictly-sequential behavior; higher values issue concurrent LLM requests,
    which is safe against remote backends like OpenRouter (each worker uses
    its own retry/backoff) but should stay conservative against a single
    self-hosted local LLM endpoint, which usually can't serve many requests
    in parallel.

    ``console`` enables a live Rich progress bar (with a persistent-history
    ETA) alongside the existing text logs. When omitted, no progress bar is
    rendered — text logs behave exactly as before.
    """
    stats = {
        "evaluated": 0,
        "skipped": 0,
        "errored": 0,
        "total_tokens": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "estimated_cost_usd": 0.0,
        "unpriced_evaluations": 0,
    }
    filter_hash = compute_filter_hash(
        project_filter=project_filter,
        limit=limit,
        open_only=open_only,
        force=force,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
    )
    query_kwargs = {
        "project_filter": project_filter,
        "open_only": open_only,
        "issue_filters": issue_filters,
        "incomplete": incomplete,
        "stale_days": stale_days,
    }

    completed_issue_ids: set[int] = set()
    checkpoint = load_checkpoint(filter_hash) if resume else None
    if checkpoint is not None and checkpoint.completed_issue_ids:
        # Only count checkpoint IDs that still match the current filters
        # (data may have changed since the checkpoint was written), rather
        # than trusting checkpoint.completed_issue_ids's length outright.
        already_done = await count_issue_evaluation_targets(
            session_factory,
            include_issue_ids=set(checkpoint.completed_issue_ids),
            **query_kwargs,
        )
        if already_done:
            completed_issue_ids = set(checkpoint.completed_issue_ids)
            stats["skipped"] = already_done
            logger.info(
                "Resuming evaluation with %d completed issues from checkpoint",
                already_done,
            )

    # Streaming + a COUNT query (rather than materializing every matching
    # issue into a Python list) keeps memory flat regardless of how many of
    # the ~19k+ issues match -- only `page_size` rows are held at a time.
    remaining_count = await count_issue_evaluation_targets(
        session_factory,
        exclude_issue_ids=completed_issue_ids or None,
        **query_kwargs,
    )
    if limit > 0:
        remaining_count = min(remaining_count, max(limit - stats["skipped"], 0))

    total_to_eval = stats["skipped"] + remaining_count

    if dry_run:
        breakdown = await fetch_issue_evaluation_breakdown(
            session_factory,
            exclude_issue_ids=completed_issue_ids or None,
            **query_kwargs,
        )
        if breakdown:
            logger.info(
                "Breakdown of issues that would be evaluated "
                "(by project and state, limit not applied):"
            )
            for (project_name, state), count in sorted(breakdown.items()):
                logger.info("  %s (%s): %d", project_name, state, count)
        logger.info("DRY RUN: %d issues would be evaluated", remaining_count)
        return {
            **stats,
            "would_evaluate": remaining_count,
        }

    if total_to_eval == 0:
        logger.info("No issues remaining to evaluate")
        if resume:
            clear_checkpoint()
        return stats

    worker_count = max(1, concurrency)
    logger.info(
        "Starting evaluation of %d issues (concurrency=%d)",
        remaining_count,
        worker_count,
    )

    quota_exhausted = False
    strict_failure: BaseException | None = None
    rate_limiter = SharedRateLimiter()
    targets_stream = stream_issue_evaluation_targets(
        session_factory,
        exclude_issue_ids=completed_issue_ids or None,
        **query_kwargs,
    )
    # Async generators are not safely re-entrant: concurrent workers calling
    # anext() on the same generator directly would race. A lock around the
    # (fast, no-LLM-call-inside) "pull next item" step keeps this safe while
    # still letting workers evaluate concurrently once they have an item.
    stream_lock = asyncio.Lock()
    next_idx = 0
    # Reserves a "budget slot" the instant an item is dispatched to a worker,
    # rather than only checking stats["evaluated"] (which is only updated
    # after the LLM call finishes). Without this, concurrency > 1 lets up to
    # (concurrency - 1) extra workers race past the limit check before any
    # of them finish, silently evaluating (and billing) more issues than
    # --limit requested. The reservation is refunded if the item resolves to
    # skipped/errored (those don't count against the limit), matching the
    # limit's single-worker semantics exactly.
    in_flight_reserved = 0

    async def _next_target() -> tuple[int, IssueEvaluationTarget] | None:
        nonlocal next_idx, in_flight_reserved
        async with stream_lock:
            if limit > 0 and stats["evaluated"] + in_flight_reserved >= limit:
                return None
            try:
                target = await anext(targets_stream)
            except StopAsyncIteration:
                return None
            next_idx += 1
            in_flight_reserved += 1
            return next_idx, target

    async def _release_reservation() -> None:
        nonlocal in_flight_reserved
        async with stream_lock:
            in_flight_reserved -= 1

    progress = None
    task_id = None
    timing: TimingHistory | None = None
    if console is not None:
        timing = TimingHistory()
        progress = make_progress(
            console, total_to_eval, timing, PHASE_EVALUATE, worker_count
        )
        task_id = progress.add_task(
            "Evaluating issues",
            total=total_to_eval,
            completed=stats["skipped"],
        )
    tracker = ProgressTracker(
        progress=progress, task_id=task_id, timing=timing, phase=PHASE_EVALUATE
    )

    async def _process_one(idx: int, target: IssueEvaluationTarget) -> None:
        nonlocal quota_exhausted, strict_failure
        t0 = time.monotonic()
        issue = target.issue
        issue_ref = f"{target.project_name}#{issue.external_id}"
        pct = (idx / total_to_eval * 100) if total_to_eval > 0 else 0
        logger.info(
            "[%d/%d] (%.0f%%) Evaluating %s: %s",
            idx,
            total_to_eval,
            pct,
            issue_ref,
            issue.title[:60],
        )
        issue_kwargs = _build_issue_kwargs(target, maintainers, force=force)

        # `outcome` stays None only on the two early-abort paths below
        # (quota exhaustion, strict-validation failure) where the item
        # didn't actually complete and shouldn't advance the progress bar
        # or trigger the trailing _log_progress() call.
        outcome: str | None = None
        try:
            result = await _evaluate_issue_with_retries(
                evaluator,
                issue_kwargs,
                issue_ref,
                rate_limiter,
            )
        except LLMQuotaError:
            logger.warning(
                "LLM quota exhausted. Stopping evaluation. %d evaluated so far.",
                stats["evaluated"],
            )
            quota_exhausted = True
            await _release_reservation()
            return
        except Exception:
            logger.exception("Error evaluating issue %s", issue.title)
            stats["errored"] += 1
            outcome = "errored"
        else:
            if result is None:
                logger.info(
                    "Skipped %s (content unchanged): %s", issue_ref, issue.title[:60]
                )
                stats["skipped"] += 1
                outcome = "skipped"
            else:
                try:
                    validate_evaluation_result(
                        result,
                        issue_type=issue.issue_type,
                        state=issue.state,
                    )
                except LLMValidationError as exc:
                    logger.warning("Validation failed for issue %s: %s", issue_ref, exc)
                    stats["errored"] += 1
                    if strict_validation:
                        strict_failure = exc
                        await _release_reservation()
                        return
                    outcome = "errored"
                else:
                    await store_evaluation_result(
                        session_factory,
                        issue_id=issue.id,
                        result=result,
                        model=evaluator.model,
                        llm_backend=llm_backend,
                    )
                    stats["evaluated"] += 1
                    stats["total_tokens"] += result["tokens_used"]
                    stats["total_prompt_tokens"] += result["prompt_tokens"]
                    stats["total_completion_tokens"] += result["completion_tokens"]
                    cost = estimate_cost_usd(
                        evaluator.model,
                        prompt_tokens=result["prompt_tokens"],
                        completion_tokens=result["completion_tokens"],
                    )
                    if cost is None:
                        stats["unpriced_evaluations"] += 1
                    else:
                        stats["estimated_cost_usd"] += cost
                    completed_issue_ids.add(issue.id)
                    if resume:
                        save_checkpoint(
                            EvaluationCheckpoint(
                                filter_hash=filter_hash,
                                completed_issue_ids=sorted(completed_issue_ids),
                                timestamp=datetime.now(tz=UTC).isoformat(),
                            )
                        )
                    outcome = "evaluated"
                    logger.info(
                        "[%d/%d] Evaluated %s (%s, %d in / %d out): %s",
                        stats["evaluated"],
                        total_to_eval,
                        issue_ref,
                        result["suggested_action"],
                        result["prompt_tokens"],
                        result["completion_tokens"],
                        issue.title[:60],
                    )

        tracker.finish(outcome, time.monotonic() - t0)
        _log_progress(
            stats,
            processed=stats["evaluated"] + stats["skipped"] + stats["errored"],
            total_to_eval=total_to_eval,
        )
        await _release_reservation()

    async def _worker() -> None:
        while True:
            if quota_exhausted or strict_failure is not None:
                return
            next_item = await _next_target()
            if next_item is None:
                return
            idx, target = next_item
            await _process_one(idx, target)

    progress_cm = progress if progress is not None else contextlib.nullcontext()
    try:
        with progress_cm:
            await asyncio.gather(*(_worker() for _ in range(worker_count)))
    finally:
        await targets_stream.aclose()

    if limit > 0 and stats["evaluated"] >= limit:
        logger.info("Reached evaluation limit of %d", limit)

    if strict_failure is not None:
        raise strict_failure

    if resume and not quota_exhausted and stats["errored"] == 0:
        clear_checkpoint()

    return stats
