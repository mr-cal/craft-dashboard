"""Untriaged issue and PR backlog counts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.services.dashboard.common import int_or_zero

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.services.dashboard.types import UntriagedBacklog


def untriaged_condition(action: str) -> ColumnElement[bool]:
    """Return the clause matching items awaiting the given suggested action."""
    return or_(
        LLMEvaluation.suggested_action == action,
        LLMEvaluation.id.is_(None),
        LLMEvaluation.suggested_action.is_(None),
    )


async def _untriaged_counts(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    thirty_days_ago: datetime,
    kind: tuple[str, str],
) -> tuple[int, int]:
    """Count untriaged open items of one issue type, plus those new in 30 days."""
    issue_type, action = kind
    query = (
        select(
            func.count(Issue.id).label("total"),
            func.count(Issue.id)
            .filter(Issue.created_at >= thirty_days_ago)
            .label("new_30d"),
        )
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest,
        )
        .where(
            Issue.state == "open",
            Issue.issue_type == issue_type,
            Project.category != "aggregate",
            untriaged_condition(action),
        )
    )
    if excl is not None:
        query = query.where(excl)
    result = (await session.execute(query)).one()
    return (
        int_or_zero(getattr(result, "total", 0)),
        int_or_zero(getattr(result, "new_30d", 0)),
    )


async def compute_untriaged_backlog(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    thirty_days_ago: datetime,
) -> UntriagedBacklog:
    """Compute untriaged issue and PR queue sizes with 30-day arrivals."""
    issues_count, issues_30d_new = await _untriaged_counts(
        session, excl, thirty_days_ago, ("issue", "needs_triage")
    )
    prs_count, prs_30d_new = await _untriaged_counts(
        session, excl, thirty_days_ago, ("pull_request", "needs_review")
    )
    return {
        "issues_count": issues_count,
        "issues_30d_new": issues_30d_new,
        "prs_count": prs_count,
        "prs_30d_new": prs_30d_new,
    }
