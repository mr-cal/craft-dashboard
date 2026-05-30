"""Admin service queries."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.refresh_schedule import RefreshSchedule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class AdminService:
    """Service for admin dashboard data access."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_token_stats(self, days: int | None = None) -> dict:
        """Get token usage statistics, optionally filtered to last N days."""
        query = select(
            func.count(LLMEvaluation.id).label("evaluations"),
            func.sum(LLMEvaluation.tokens_used).label("tokens"),
            func.sum(LLMEvaluation.prompt_tokens).label("prompt_tokens"),
            func.sum(LLMEvaluation.completion_tokens).label("completion_tokens"),
        )
        if days is not None:
            query = query.where(
                LLMEvaluation.evaluated_at >= datetime.now(UTC) - timedelta(days=days)
            )

        row = (await self.session.execute(query)).one()
        return {
            "evaluations": row.evaluations or 0,
            "tokens": row.tokens or 0,
            "prompt_tokens": row.prompt_tokens or 0,
            "completion_tokens": row.completion_tokens or 0,
        }

    async def get_lifetime_token_stats(self) -> dict:
        """Get lifetime token usage statistics."""
        return await self.get_token_stats()

    async def get_seven_day_token_stats(self) -> dict:
        """Get token usage for the last 7 days."""
        return await self.get_token_stats(days=7)

    async def get_schedule(self) -> list[dict]:
        """Get the refresh schedule grouped by project."""
        result = await self.session.execute(
            select(Project.name, RefreshSchedule.next_refresh_at)
            .join(RefreshSchedule, RefreshSchedule.project_id == Project.id)
            .where(Project.category != "aggregate")
            .where(RefreshSchedule.next_refresh_at.is_not(None))
            .order_by(
                Project.display_order, Project.name, RefreshSchedule.next_refresh_at
            )
        )

        grouped: dict[str, set[int]] = defaultdict(set)
        for row in result:
            grouped[row.name].add(row.next_refresh_at.weekday())

        return [
            {"project": project, "days": sorted(days)}
            for project, days in grouped.items()
        ]

    async def get_schedule_day_counts(self, days_ahead: int = 7) -> list[dict]:
        """Get upcoming schedule counts for the next N days."""
        now = datetime.now(UTC)
        schedule_days = []
        for day_offset in range(days_ahead):
            day_start = (now + timedelta(days=day_offset)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = day_start + timedelta(days=1)
            issue_count = (
                await self.session.scalar(
                    select(func.count(Issue.id))
                    .join(
                        RefreshSchedule,
                        Issue.project_id == RefreshSchedule.project_id,
                    )
                    .where(RefreshSchedule.next_refresh_at >= day_start)
                    .where(RefreshSchedule.next_refresh_at < day_end)
                )
                or 0
            )
            schedule_days.append(
                {
                    "date": day_start.strftime("%a %b %d"),
                    "count": issue_count,
                    "is_today": day_offset == 0,
                }
            )
        return schedule_days

    async def get_project_names(self) -> list[str]:
        """Get all non-aggregate project names."""
        project_result = await self.session.execute(
            select(Project.name)
            .where(Project.category != "aggregate")
            .order_by(Project.display_order)
        )
        return [row.name for row in project_result]

    async def update_schedule(self, project: str, days: list[int]) -> None:
        """Update the refresh schedule for a project."""
        result = await self.session.execute(
            select(RefreshSchedule)
            .join(Project, Project.id == RefreshSchedule.project_id)
            .where(Project.name == project)
            .order_by(RefreshSchedule.source)
        )
        schedules = list(result.scalars())
        if not schedules:
            return

        now = datetime.now(UTC)
        normalized_days = sorted(days)
        if not normalized_days:
            for schedule in schedules:
                schedule.next_refresh_at = None
            await self.session.commit()
            return

        for index, schedule in enumerate(schedules):
            schedule.next_refresh_at = _next_occurrence(
                now,
                normalized_days[index % len(normalized_days)],
            )

        await self.session.commit()


def _next_occurrence(now: datetime, target_day: int) -> datetime:
    """Return the next occurrence of the target weekday preserving time."""
    day_offset = (target_day - now.weekday()) % 7
    return now + timedelta(days=day_offset)
