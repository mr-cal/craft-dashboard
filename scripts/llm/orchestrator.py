"""Orchestration for running LLM evaluations over issues."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict

from craft_dashboard.llm.exceptions import LLMQuotaError
from scripts.llm.queries import IssueFilter, fetch_issue_evaluation_targets
from scripts.llm.storage import store_evaluation_result

if TYPE_CHECKING:
    from craft_dashboard.llm.evaluator import IssueEvaluator
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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


def _log_progress(
    stats: EvaluationStats, *, processed: int, total_to_eval: int
) -> None:
    if processed % 10 == 0:
        logger.info(
            "Progress [%d/%d]: %d evaluated, %d skipped, %d errors",
            processed,
            total_to_eval,
            stats["evaluated"],
            stats["skipped"],
            stats["errored"],
        )


async def _evaluate_issues(  # noqa: PLR0913
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
) -> EvaluationStats | DryRunEvaluationStats:
    """Evaluate matched issues and persist any new results."""
    stats = {"evaluated": 0, "skipped": 0, "errored": 0, "total_tokens": 0}
    targets = await fetch_issue_evaluation_targets(
        session_factory,
        project_filter=project_filter,
        open_only=open_only,
        issue_filters=issue_filters,
        incomplete=incomplete,
        stale_days=stale_days,
    )

    if dry_run:
        logger.info("DRY RUN: %d issues would be evaluated", len(targets))
        return {
            **stats,
            "would_evaluate": len(targets),
        }

    total_to_eval = len(targets)
    logger.info("Starting evaluation of %d issues", total_to_eval)

    for idx, target in enumerate(targets, start=1):
        issue = target.issue
        existing_hash = None if force else target.issue_data_hash
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

        issue_ref = f"{target.project_name}#{issue.external_id}"
        logger.info(
            "[%d/%d] Evaluating %s: %s", idx, total_to_eval, issue_ref, issue.title[:60]
        )

        try:
            result = await evaluator.evaluate_issue(
                title=issue.title,
                body=issue.body,
                issue_type=issue.issue_type,
                labels=labels,
                age_days=age_days,
                last_activity_days=last_activity_days,
                author=issue.author or "unknown",
                is_maintainer=issue.author in maintainers if issue.author else False,
                comment_count=len(issue_comments),
                comments=issue_comments,
                pr_details=pr_details,
                existing_hash=existing_hash,
            )
        except LLMQuotaError:
            logger.warning(
                "LLM quota exhausted. Stopping evaluation. %d evaluated so far.",
                stats["evaluated"],
            )
            break
        except Exception:
            logger.exception("Error evaluating issue %s", issue.title)
            stats["errored"] += 1
            _log_progress(
                stats,
                processed=stats["evaluated"] + stats["skipped"] + stats["errored"],
                total_to_eval=total_to_eval,
            )
            continue

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
            continue

        await store_evaluation_result(
            session_factory,
            issue_id=issue.id,
            result=result,
            evaluation_model=evaluator.evaluation_model,
            llm_backend=llm_backend,
        )

        stats["evaluated"] += 1
        stats["total_tokens"] += result["tokens_used"]
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

        if limit > 0 and stats["evaluated"] >= limit:
            logger.info("Reached evaluation limit of %d", limit)
            break

    return stats
