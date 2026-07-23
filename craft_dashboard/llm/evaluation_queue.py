"""Query for finding the next issue that needs LLM evaluation.

Extracted from ``craft_dashboard.routes.eval_api`` so the "what's pending"
logic is independently testable and reusable, and so it's the single place
that encodes how ``Issue.content_hash`` (denormalized, kept up to date by the
collectors — see ``craft_dashboard.llm.content_hash``) is used to detect
content drift without recomputing a hash for every issue on every poll.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import aliased, defer

from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.repositories.issue_repository import (
    _build_excluded_issues_condition,
)

if TYPE_CHECKING:
    from sqlalchemy.sql import Select


def build_pending_evaluation_query(
    *,
    project: str = "",
    open_only: bool = True,
    force: bool = False,
    incomplete: bool = False,
    stale_days: int = 0,
    external_id: str = "",
    filtered_issues: dict[str, list[str]] | None = None,
    now: datetime | None = None,
) -> Select[tuple[Issue, str, LLMEvaluation | None]]:
    """Build a query returning the single highest-priority pending issue.

    Priority order: never evaluated (open first), then stale eval_version
    (open first), then everything else — content-hash mismatches, force,
    incomplete, and stale_days re-checks fall out of the ``needs_evaluation``
    filter below rather than needing their own priority tier, since they're
    exceptional/manually-requested cases, not the steady-state queue.

    The caller is expected to ``.limit(1)`` this query (or iterate it if it
    wants more than one candidate) — this module intentionally returns a
    ``Select`` rather than executing it, so callers control session/limit.

    Args:
        project: Restrict to this project name, if set.
        open_only: Restrict to open issues.
        force: Return issues even if already fully evaluated at the current
            content hash and eval version.
        incomplete: Restrict to issues with a missing/incomplete evaluation.
        stale_days: Also return issues last evaluated more than this many
            days ago, even if content/version otherwise match.
        external_id: Restrict to a single issue by external_id (requires
            project).
        filtered_issues: Per-project issue numbers to exclude entirely.
        now: Override "now" for locking/staleness comparisons (tests).

    Returns:
        A ``Select`` yielding ``(Issue, project_name, latest_evaluation)``
        rows, ordered by priority, with the embedding column deferred.

    """
    now = now or datetime.now(tz=UTC)
    latest_evaluation = aliased(LLMEvaluation)

    old_version = or_(
        latest_evaluation.eval_version.is_(None),
        latest_evaluation.eval_version != CURRENT_EVAL_VERSION,
    )
    priority = case(
        (latest_evaluation.id.is_(None) & (Issue.state == "open"), 1),
        (latest_evaluation.id.is_(None) & (Issue.state != "open"), 2),
        (old_version & (Issue.state == "open"), 3),
        (old_version & (Issue.state != "open"), 4),
        else_=5,
    )
    open_first = case((Issue.state == "open", 0), else_=1)

    query = (
        select(Issue, Project.name, latest_evaluation)
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            latest_evaluation,
            (latest_evaluation.issue_id == Issue.id) & latest_evaluation.latest,
        )
        .where(
            or_(
                latest_evaluation.eval_locked_until.is_(None),
                latest_evaluation.eval_locked_until <= now,
            )
        )
        .order_by(priority, open_first, Issue.id)
        # summary_embedding is a Vector(1024) column (~4KB/row). This query
        # never needs the embedding value, so defer it to avoid transferring
        # it for every candidate row.
        .options(defer(latest_evaluation.summary_embedding))
    )

    excl = _build_excluded_issues_condition(filtered_issues or {})
    if excl is not None:
        query = query.where(excl)

    if open_only:
        query = query.where(Issue.state == "open")

    if project:
        query = query.where(Project.name == project)

    if external_id:
        query = query.where(Issue.external_id == external_id)

    if incomplete:
        query = query.where(
            or_(
                latest_evaluation.id.is_(None),
                latest_evaluation.summary.is_(None),
                latest_evaluation.summary == "",
                (
                    (Issue.state == "open")
                    & or_(
                        latest_evaluation.scores.is_(None),
                        latest_evaluation.suggested_action.is_(None),
                        latest_evaluation.suggested_action == "",
                    )
                ),
            )
        )

    if stale_days > 0:
        cutoff = now - timedelta(days=stale_days)
        query = query.where(
            or_(
                latest_evaluation.id.is_(None),
                latest_evaluation.evaluated_at < cutoff,
            )
        )

    # Everything above narrows the *candidate set*; this narrows it further
    # to exclude issues that are already fully, currently evaluated — the
    # steady-state case that must be a cheap column comparison, not a
    # per-row hash recomputation. Only applied when force/incomplete/
    # stale_days aren't already asking for a broader re-check.
    if not force and stale_days <= 0 and not incomplete:
        has_complete_evaluation = and_(
            latest_evaluation.id.is_not(None),
            latest_evaluation.summary.is_not(None),
            latest_evaluation.summary != "",
            or_(
                Issue.state != "open",
                and_(
                    latest_evaluation.scores.is_not(None),
                    latest_evaluation.suggested_action.is_not(None),
                    latest_evaluation.suggested_action != "",
                ),
            ),
        )
        is_up_to_date = and_(
            has_complete_evaluation,
            latest_evaluation.eval_version == CURRENT_EVAL_VERSION,
            ~latest_evaluation.issue_data_hash.is_distinct_from(Issue.content_hash),
        )
        query = query.where(~is_up_to_date)

    # Concurrent workers (``--concurrency > 1``) each call ``/next`` in
    # parallel. Without row locking here, two workers can both SELECT the
    # same candidate issue before either commits the lock-until update in
    # ``next_issue``, causing the same issue to be evaluated twice (wasted
    # tokens, and a spurious second write that flips ``latest`` back and
    # forth). ``FOR UPDATE SKIP LOCKED`` on the ``Issue`` row makes a
    # concurrent transaction skip an issue that's mid-selection by another
    # worker and move on to the next candidate instead of blocking or
    # double-picking it. ``of=Issue`` is required because of the outer join
    # to ``latest_evaluation``, which may have no matching row (never
    # evaluated) — only ``Issue`` rows always exist to lock. SQLite (used in
    # tests) silently ignores ``with_for_update``.
    query = query.with_for_update(skip_locked=True, of=Issue)

    return cast("Select[tuple[Issue, str, LLMEvaluation | None]]", query)
