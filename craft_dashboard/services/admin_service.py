"""Admin service queries."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypedDict

from sqlalchemy import String, cast, func, select

from craft_dashboard.collectors.github import GitHubCollector, RateLimitStatus
from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_activity import IssueActivity
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.refresh_schedule import RefreshSchedule
from craft_dashboard.settings import Settings

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


class ProjectRefreshEntry(TypedDict):
    """Per-project full-refresh schedule entry shown on the admin dashboard."""

    project: str
    next_refresh_at: datetime | None
    last_refreshed_at: datetime | None
    consecutive_failures: int
    is_overdue: bool
    days_until_next: int | None
    days_since_last: int | None


class ScheduleDayCount(TypedDict):
    """Upcoming scheduled issue count for a calendar day."""

    date: str
    count: int
    is_today: bool


class CollectionRunSummary(TypedDict):
    """Recent collection run summary shown on the admin dashboard."""

    id: int
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


class ActivityEntry(TypedDict):
    """A single issue/PR event.

    Shared by the recent-activity feed and the per-run detail view so both
    render identical columns.
    """

    project: str
    number: str
    title: str
    url: str | None
    issue_type: str
    change_type: str
    occurred_at: datetime


_EVALUATION_SOURCES = {"llm", "evaluation"}
_OPEN_POLL_INTERVAL = timedelta(minutes=10)


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

    async def get_project_refresh_list(self) -> list[ProjectRefreshEntry]:
        """Get per-project full-refresh schedule sorted by next_refresh_at.

        Returns projects with their scheduled full-refresh timing (open+closed
        issues). Projects with overdue or missing schedules appear first.
        """
        result = await self.session.execute(
            select(
                Project.name,
                RefreshSchedule.next_refresh_at,
                RefreshSchedule.last_refreshed_at,
                RefreshSchedule.consecutive_failures,
            )
            .join(RefreshSchedule, RefreshSchedule.project_id == Project.id)
            .where(Project.category != "aggregate")
            .where(RefreshSchedule.source == "github")
            .order_by(
                RefreshSchedule.next_refresh_at.asc().nullsfirst(),
                Project.name,
            )
        )
        return [
            {
                "project": row.name,
                "next_refresh_at": _ensure_utc(row.next_refresh_at),
                "last_refreshed_at": _ensure_utc(row.last_refreshed_at),
                "consecutive_failures": row.consecutive_failures,
                "is_overdue": self._is_overdue(row.next_refresh_at),
                "days_until_next": _days_delta(_ensure_utc(row.next_refresh_at)),
                "days_since_last": _days_delta(_ensure_utc(row.last_refreshed_at)),
            }
            for row in result
        ]

    def _is_overdue(self, next_refresh_at: datetime | None) -> bool:
        """Return True if next_refresh_at is in the past."""
        if next_refresh_at is None:
            return False
        utc_ts = _ensure_utc(next_refresh_at)
        return utc_ts is not None and utc_ts <= datetime.now(UTC)

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
        self, limit: int = 20
    ) -> list[CollectionRunSummary]:
        """Get the most recent collection runs with health statistics."""
        result = await self.session.execute(
            select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(limit)
        )
        runs = list(result.scalars())
        return [
            {
                "id": run.id,
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

    async def get_recent_issue_activity(self, limit: int = 50) -> list[ActivityEntry]:
        """Return the most recent issue/PR change events, newest first.

        Joined against ``Issue`` for the live GitHub URL; falls back to the
        title recorded at the time of the change if the issue row itself
        was later removed (e.g. project deletion).
        """
        rows = await self.session.execute(
            select(
                IssueActivity,
                Project.name.label("project_name"),
                Issue.url,
                Issue.issue_type,
            )
            .join(Project, Project.id == IssueActivity.project_id)
            .outerjoin(
                Issue,
                (Issue.project_id == IssueActivity.project_id)
                & (Issue.source == "github")
                & (Issue.external_id == cast(IssueActivity.issue_number, String)),
            )
            .order_by(IssueActivity.occurred_at.desc())
            .limit(limit)
        )
        return [
            {
                "project": row.project_name,
                "number": str(row.IssueActivity.issue_number),
                "title": row.IssueActivity.title,
                "url": row.url,
                "issue_type": row.issue_type or "issue",
                "change_type": row.IssueActivity.change_type,
                "occurred_at": row.IssueActivity.occurred_at,
            }
            for row in rows
        ]

    async def get_api_budget(self) -> RateLimitStatus:
        """Return the current REST and GraphQL API budgets, for the admin page."""
        settings = Settings()
        collector = GitHubCollector(token=settings.github_token)
        return await asyncio.to_thread(collector.check_rate_limit)

    async def get_next_expected_fetch(self) -> datetime | None:
        """Approximate when the next GitHub open-issue poll is expected."""
        # CollectionRun currently records only source="github", not whether the
        # run was the 10-minute open poll or the daily full refresh. This makes
        # the admin indicator a best-effort approximation: immediately after a
        # full-refresh run completes, the computed timestamp can be off by up to
        # one open-poll interval until the next open-mode run completes.
        last_run = await self.session.scalar(
            select(CollectionRun)
            .where(CollectionRun.source == "github")
            .where(CollectionRun.status == "completed")
            .order_by(CollectionRun.started_at.desc())
            .limit(1)
        )
        if last_run is None:
            return None
        # Open-issue polling follows the GitHub collector cadence, not other sources.
        started_at = _ensure_utc(last_run.started_at)
        if started_at is None:
            return None
        return started_at + _OPEN_POLL_INTERVAL

    async def get_issues_for_run(
        self,
        run_id: int,
        limit: int = 100,
    ) -> tuple[list[ActivityEntry], int]:
        """Return up to *limit* issues belonging to a collection run and the total count.

        Args:
            run_id: Primary key of the collection run.
            limit: Maximum number of issues to return.

        Returns:
            A tuple of (issue list, total count).  The list is sorted by project
            name then issue title and capped at *limit* entries. Issues that
            weren't actually changed by this run (unchanged since last fetch)
            are included with change_type "unchanged".

        """
        total_result = await self.session.execute(
            select(func.count())
            .select_from(Issue)
            .where(Issue.collection_run_id == run_id)
        )
        total = total_result.scalar_one()

        rows_result = await self.session.execute(
            select(
                Issue,
                Project.name.label("project_name"),
                IssueActivity.change_type,
                IssueActivity.occurred_at,
            )
            .join(Project, Issue.project_id == Project.id)
            .outerjoin(
                IssueActivity,
                (IssueActivity.collection_run_id == run_id)
                & (IssueActivity.project_id == Issue.project_id)
                & (cast(IssueActivity.issue_number, String) == Issue.external_id),
            )
            .where(Issue.collection_run_id == run_id)
            .order_by(Project.name, Issue.title)
            .limit(limit)
        )
        issues: list[ActivityEntry] = [
            {
                "project": row.project_name,
                "number": row.Issue.external_id,
                "title": row.Issue.title,
                "url": row.Issue.url,
                "issue_type": row.Issue.issue_type,
                "change_type": row.change_type or "unchanged",
                "occurred_at": row.occurred_at or row.Issue.last_fetched_at,
            }
            for row in rows_result
        ]
        return issues, total

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


def _days_delta(ts: datetime | None) -> int | None:
    """Return whole days from now to *ts* (positive = future, negative = past)."""
    if ts is None:
        return None
    return (ts - datetime.now(UTC)).days
