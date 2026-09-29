"""Homepage attention lists: aging PRs, triage queue, and quick wins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.services.dashboard.common import days_since
from craft_dashboard.services.dashboard.untriaged import untriaged_condition

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.services.dashboard.types import (
        AgingPR,
        NeedsTriageIssue,
        QuickWinIssue,
    )

QUICK_WIN_MIN_SCORE = 50
SPOTLIGHT_LIMIT = 5


def _days_old(now: datetime, created_at: datetime | None) -> int:
    """Return the age in days of an item, or 0 when its creation is unknown."""
    return days_since(now, created_at) if created_at else 0


async def compute_aging_prs(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    now: datetime,
) -> list[AgingPR]:
    """Fetch the oldest open contributor PRs."""
    query = (
        select(
            Project.name.label("project_name"),
            Issue.external_id,
            Issue.title,
            Issue.author,
            Issue.created_at,
            Issue.url,
        )
        .join(Project, Issue.project_id == Project.id)
        .where(
            Issue.state == "open",
            Issue.issue_type == "pull_request",
            Issue.author_is_maintainer.is_(False),
            Issue.author_is_bot.is_(False),
            Project.category != "aggregate",
        )
    )
    if excl is not None:
        query = query.where(excl)
    query = query.order_by(Issue.created_at.asc()).limit(SPOTLIGHT_LIMIT)
    rows = (await session.execute(query)).all()

    return [
        {
            "project_name": row.project_name,
            "external_id": row.external_id,
            "title": row.title,
            "author": row.author,
            "days_old": _days_old(now, row.created_at),
            "url": row.url,
        }
        for row in rows
    ]


async def compute_needs_triage_issues(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    now: datetime,
) -> list[NeedsTriageIssue]:
    """Fetch the oldest open issues still awaiting triage."""
    query = (
        select(
            Project.name.label("project_name"),
            Issue.external_id,
            Issue.title,
            Issue.author,
            Issue.created_at,
            Issue.url,
        )
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest,
        )
        .where(
            Issue.state == "open",
            Issue.issue_type == "issue",
            Project.category != "aggregate",
            untriaged_condition("needs_triage"),
        )
    )
    if excl is not None:
        query = query.where(excl)
    query = query.order_by(Issue.created_at.asc()).limit(SPOTLIGHT_LIMIT)
    rows = (await session.execute(query)).all()

    return [
        {
            "project_name": row.project_name,
            "external_id": row.external_id,
            "title": row.title,
            "author": row.author,
            "days_old": _days_old(now, row.created_at),
            "url": row.url,
        }
        for row in rows
    ]


async def compute_quick_wins(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
) -> list[QuickWinIssue]:
    """Fetch the highest-scoring open quick-win issues."""
    query = (
        select(
            Project.name.label("project_name"),
            Issue.external_id,
            Issue.title,
            Issue.url,
            LLMEvaluation.scores,
        )
        .join(Project, Issue.project_id == Project.id)
        .join(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest,
        )
        .where(
            Issue.state == "open",
            Issue.issue_type == "issue",
            Project.category != "aggregate",
            LLMEvaluation.scores.is_not(None),
        )
    )
    if excl is not None:
        query = query.where(excl)
    rows = (await session.execute(query)).all()

    candidates: list[QuickWinIssue] = []
    for row in rows:
        scores = row.scores or {}
        qw_raw = scores.get("quick_win")
        if qw_raw is None:
            continue
        try:
            qw_val = int(round(float(qw_raw)))
        except (ValueError, TypeError):
            qw_val = 0
        if qw_val >= QUICK_WIN_MIN_SCORE:
            candidates.append(
                {
                    "project_name": row.project_name,
                    "external_id": row.external_id,
                    "title": row.title,
                    "quick_win": qw_val,
                    "impact": scores.get("impact"),
                    "complexity": scores.get("complexity"),
                    "url": row.url,
                }
            )
    candidates.sort(key=lambda x: x["quick_win"], reverse=True)
    return candidates[:SPOTLIGHT_LIMIT]
