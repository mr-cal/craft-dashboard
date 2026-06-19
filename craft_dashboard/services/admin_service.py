"""Admin service queries."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypedDict

from sqlalchemy import func, select

from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.refresh_schedule import RefreshSchedule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class TokenStats(TypedDict):
    """Token usage totals shown on the admin dashboard."""

    evaluations: int
    tokens: int
    prompt_tokens: int
    completion_tokens: int


class ProjectSchedule(TypedDict):
    """Weekday schedule summary for a project."""

    project: str
    days: list[int]


class ScheduleDayCount(TypedDict):
    """Upcoming scheduled issue count for a calendar day."""

    date: str
    count: int
    is_today: bool


class CollectionRunSummary(TypedDict):
    """Recent collection run summary shown on the admin dashboard."""

    source: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    projects_processed: int
    issues_collected: int
    duration_seconds: float | None
    errors: list[dict]


class SystemStatus(TypedDict):
    """Live collection and evaluation status payload."""

    collection_running: bool
    evaluation_running: bool
    last_collection: datetime | None
    last_evaluation: datetime | None


_EVALUATION_SOURCES = {"llm", "evaluation"}


def _ensure_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to naive timestamps returned by SQLite tests."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class AdminService:
    """Service for admin dashboard data access."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_token_stats(self, days: int | None = None) -> TokenStats:
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

    async def get_lifetime_token_stats(self) -> TokenStats:
        """Get lifetime token usage statistics."""
        return await self.get_token_stats()

    async def get_seven_day_token_stats(self) -> TokenStats:
        """Get token usage for the last 7 days."""
        return await self.get_token_stats(days=7)

    async def get_schedule(self) -> list[ProjectSchedule]:
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

    async def get_schedule_day_counts(
        self, days_ahead: int = 7
    ) -> list[ScheduleDayCount]:
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

    async def get_next_scheduled_refresh(self) -> datetime | None:
        """Return the earliest future next_refresh_at across all schedules."""
        result = await self.session.scalar(
            select(func.min(RefreshSchedule.next_refresh_at)).where(
                RefreshSchedule.next_refresh_at > datetime.now(UTC)
            )
        )
        if not isinstance(result, datetime):
            return None
        return _ensure_utc(result)

    async def get_project_names(self) -> list[str]:
        """Get all non-aggregate project names."""
        project_result = await self.session.execute(
            select(Project.name)
            .where(Project.category != "aggregate")
            .order_by(Project.display_order)
        )
        return [row.name for row in project_result]

    async def get_recent_collection_runs(
        self, limit: int = 10
    ) -> list[CollectionRunSummary]:
        """Get the most recent collection runs with health statistics."""
        result = await self.session.execute(
            select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(limit)
        )
        runs = list(result.scalars())
        return [
            {
                "source": run.source,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "status": run.status,
                "projects_processed": run.projects_processed,
                "issues_collected": run.issues_collected,
                "duration_seconds": run.duration_seconds,
                "errors": run.errors or [],
            }
            for run in runs
        ]

    async def get_system_status(self) -> SystemStatus:
        """Get current system status (running processes, last run times)."""
        running_sources = set(
            await self.session.scalars(
                select(CollectionRun.source).where(CollectionRun.status == "running")
            )
        )
        last_collection = await self.session.scalar(
            select(
                func.max(
                    func.coalesce(CollectionRun.finished_at, CollectionRun.started_at)
                )
            ).where(~CollectionRun.source.in_(_EVALUATION_SOURCES))
        )
        last_evaluation = await self.session.scalar(
            select(func.max(LLMEvaluation.evaluated_at))
        )
        return {
            "collection_running": any(
                source not in _EVALUATION_SOURCES for source in running_sources
            ),
            "evaluation_running": any(
                source in _EVALUATION_SOURCES for source in running_sources
            ),
            "last_collection": _ensure_utc(last_collection),
            "last_evaluation": _ensure_utc(last_evaluation),
        }

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
