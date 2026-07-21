"""Orchestration for running LLM evaluations over issues."""

from __future__ import annotations

import asyncio
import logging
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

from scripts.llm.checkpoint import (
    EvaluationCheckpoint,
    clear_checkpoint,
    compute_filter_hash,
    load_checkpoint,
    save_checkpoint,
)
from scripts.llm.queries import IssueFilter, fetch_issue_evaluation_targets
from scripts.llm.storage import store_evaluation_result
from scripts.llm.validation import validate_evaluation_result

if TYPE_CHECKING:
    from craft_dashboard.llm.evaluator import EvaluationResult, IssueEvaluator
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from scripts.llm.queries import IssueEvaluationTarget


class EvaluationStats(TypedDict):
    """Progress counters returned by the LLM orchestration loop."""

    evaluated: int
    skipped: int
    errored: int
    total_tokens: int


class DryRunEvaluationStats(EvaluationStats):
    """Evaluation counters returned when running in dry-run mode."""

    would_evaluate: int


logger = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})
_retry_sleep = asyncio.sleep


def _is_retryable_evaluation_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return isinstance(exc, (httpx.TimeoutException, LLMTimeoutError, LLMRateLimitError))


def _log_retry_attempt(retry_state: RetryCallState, *, issue_ref: str) -> None:
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


async def _evaluate_issue_with_retries(
    evaluator: IssueEvaluator,
    issue_kwargs: dict[str, object],
    issue_ref: str,
) -> EvaluationResult | None:
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(_is_retryable_evaluation_error),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        stop=stop_after_attempt(3),
        before_sleep=lambda retry_state: _log_retry_attempt(
            retry_state, issue_ref=issue_ref
        ),
        sleep=_retry_sleep,
        reraise=True,
    ):
        with attempt:
            return await evaluator.evaluate(**issue_kwargs)
    msg = f"Retry loop exited unexpectedly for {issue_ref}"
    raise RuntimeError(msg)


def _log_progress(
    stats: EvaluationStats, *, processed: int, total_to_eval: int
) -> None:
    if processed % 10 == 0:
        pct = (processed / total_to_eval * 100) if total_to_eval > 0 else 0
        logger.info(
            "Progress [%d/%d] (%.0f%%): %d evaluated, %d skipped, %d errors",
            processed,
            total_to_eval,
            pct,
            stats["evaluated"],
            stats["skipped"],
            stats["errored"],
        )


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
) -> EvaluationStats | DryRunEvaluationStats:
    """Evaluate matched issues and persist any new results.

    ``concurrency`` controls how many issues are evaluated at once (workers
    pull from a shared queue of targets). A value of 1 preserves the original
    strictly-sequential behavior; higher values issue concurrent LLM requests,
    which is safe against remote backends like OpenRouter (each worker uses
    its own retry/backoff) but should stay conservative against a single
    self-hosted local LLM endpoint, which usually can't serve many requests
    in parallel.
    """
    stats = {"evaluated": 0, "skipped": 0, "errored": 0, "total_tokens": 0}
    filter_hash = compute_filter_hash(
        project_filter=project_filter,
        limit=limit,
        open_only=open_only,
        force=force,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
    )
    targets = await fetch_issue_evaluation_targets(
        session_factory,
        project_filter=project_filter,
        open_only=open_only,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
    )

    completed_issue_ids: set[int] = set()
    checkpoint = load_checkpoint(filter_hash) if resume else None
    if checkpoint is not None:
        target_ids = {target.issue.id for target in targets}
        completed_issue_ids = {
            issue_id
            for issue_id in checkpoint.completed_issue_ids
            if issue_id in target_ids
        }
        if completed_issue_ids:
            logger.info(
                "Resuming evaluation with %d completed issues from checkpoint",
                len(completed_issue_ids),
            )
            stats["skipped"] = len(completed_issue_ids)
            targets = [
                target
                for target in targets
                if target.issue.id not in completed_issue_ids
            ]

    if limit > 0:
        remaining_limit = max(limit - len(completed_issue_ids), 0)
        targets = targets[:remaining_limit]

    total_to_eval = stats["skipped"] + len(targets)

    if dry_run:
        logger.info("DRY RUN: %d issues would be evaluated", len(targets))
        return {
            **stats,
            "would_evaluate": len(targets),
        }

    if total_to_eval == 0:
        logger.info("No issues remaining to evaluate")
        if resume:
            clear_checkpoint()
        return stats

    worker_count = max(1, concurrency)
    logger.info(
        "Starting evaluation of %d issues (concurrency=%d)",
        len(targets),
        worker_count,
    )

    quota_exhausted = False
    strict_failure: BaseException | None = None
    # A shared, non-reentrant iterator: asyncio workers pull the next
    # (idx, target) pair cooperatively. Calling next() is synchronous with no
    # `await` inside it, so concurrent workers can never race on the same item
    # even though they all share this one iterator object.
    remaining = iter(enumerate(targets, start=1))

    async def _process_one(idx: int, target: IssueEvaluationTarget) -> None:
        nonlocal quota_exhausted, strict_failure

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

        issue_kwargs = {
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

        try:
            result = await _evaluate_issue_with_retries(
                evaluator,
                issue_kwargs,
                issue_ref,
            )
        except LLMQuotaError:
            logger.warning(
                "LLM quota exhausted. Stopping evaluation. %d evaluated so far.",
                stats["evaluated"],
            )
            quota_exhausted = True
            return
        except Exception:
            logger.exception("Error evaluating issue %s", issue.title)
            stats["errored"] += 1
            _log_progress(
                stats,
                processed=stats["evaluated"] + stats["skipped"] + stats["errored"],
                total_to_eval=total_to_eval,
            )
            return

        if result is None:
            logger.info(
                "Skipped %s (content unchanged): %s", issue_ref, issue.title[:60]
            )
            stats["skipped"] += 1
            _log_progress(
                stats,
                processed=stats["evaluated"] + stats["skipped"] + stats["errored"],
                total_to_eval=total_to_eval,
            )
            return

        try:
            validate_evaluation_result(
                result,
                issue_type=issue.issue_type,
                state=issue.state,
            )
        except LLMValidationError as exc:
            logger.warning("Validation failed for issue %s", issue_ref, exc_info=True)
            stats["errored"] += 1
            if strict_validation:
                strict_failure = exc
                return
            _log_progress(
                stats,
                processed=stats["evaluated"] + stats["skipped"] + stats["errored"],
                total_to_eval=total_to_eval,
            )
            return

        await store_evaluation_result(
            session_factory,
            issue_id=issue.id,
            result=result,
            model=evaluator.model,
            llm_backend=llm_backend,
        )

        stats["evaluated"] += 1
        stats["total_tokens"] += result["tokens_used"]
        completed_issue_ids.add(issue.id)
        if resume:
            save_checkpoint(
                EvaluationCheckpoint(
                    filter_hash=filter_hash,
                    completed_issue_ids=sorted(completed_issue_ids),
                    timestamp=datetime.now(tz=UTC).isoformat(),
                )
            )
        logger.info(
            "[%d/%d] Evaluated %s (%s, %d tokens): %s",
            stats["evaluated"],
            total_to_eval,
            issue_ref,
            result["suggested_action"],
            result["tokens_used"],
            issue.title[:60],
        )
        _log_progress(
            stats,
            processed=stats["evaluated"] + stats["skipped"] + stats["errored"],
            total_to_eval=total_to_eval,
        )

    async def _worker() -> None:
        while True:
            if quota_exhausted or strict_failure is not None:
                return
            if limit > 0 and stats["evaluated"] >= limit:
                return
            try:
                idx, target = next(remaining)
            except StopIteration:
                return
            await _process_one(idx, target)

    await asyncio.gather(*(_worker() for _ in range(worker_count)))

    if limit > 0 and stats["evaluated"] >= limit:
        logger.info("Reached evaluation limit of %d", limit)

    if strict_failure is not None:
        raise strict_failure

    if resume and not quota_exhausted and stats["errored"] == 0:
        clear_checkpoint()

    return stats
