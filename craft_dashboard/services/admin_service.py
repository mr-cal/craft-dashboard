"""Admin service queries."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypedDict
from typing import cast as typing_cast

from sqlalchemy import String, cast, func, or_, select

from craft_dashboard.collectors.github import GitHubCollector, RateLimitStatus
from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.models.eval_queue_snapshot import EvalQueueSnapshot
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_activity import IssueActivity
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.refresh_schedule import RefreshSchedule
from craft_dashboard.repositories.issue_repository import (
    _build_excluded_issues_condition,
)
from craft_dashboard.routes.eval_api import (
    _ACTIVITY_STALE_AFTER,
    get_eval_activity,
    get_quota_pause_until,
)
from craft_dashboard.settings import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement


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


class LLMServiceStatus(TypedDict):
    """Status of the continuous `evaluate` worker, derived from HTTP activity.

    No direct process/heartbeat signal exists from the worker itself (it's a
    remote HTTP client, possibly not even on this host) — status is inferred
    from how recently it has called `/api/eval/next` or `/api/eval/result`,
    plus any self-reported quota-pause (see ``report_quota_pause``).
    """

    status: str  # "running" | "stalled" | "stalled_quota" | "unknown"
    last_poll_at: datetime | None
    last_result_at: datetime | None
    quota_resume_at: datetime | None


class RecentEvaluationEntry(TypedDict):
    """A single recently-submitted evaluation shown on the admin dashboard.

    Deliberately excludes token/cost fields — the admin page doesn't surface
    OpenRouter spend.
    """

    issue_id: int
    project: str
    external_id: str
    title: str
    model_name: str
    suggested_action: str | None
    evaluated_at: datetime


class DailyEvaluationCount(TypedDict):
    """Count of evaluations submitted on a given calendar day."""

    date: str
    count: int


class QueueDepthPoint(TypedDict):
    """A single sampled point of eval queue depth, for charting over time."""

    captured_at: datetime
    pending_count: int
    total_open: int
    evaluated_today: int


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


def _build_excluded_activity_condition(
    filtered_issues: dict[str, list[str]],
) -> ColumnElement[bool] | None:
    """Return a NOT(...) clause excluding configured issue numbers from IssueActivity.

    Mirrors ``issue_repository._build_excluded_issues_condition`` but matches on
    ``IssueActivity.issue_number`` (an integer column) instead of
    ``Issue.external_id``, since the activity feed query only outer-joins
    ``Issue`` (the issue row may no longer exist) and must not depend on that
    join to apply the exclusion. Requires ``Project`` to already be joined in
    the calling query. Returns None when filtered_issues is empty.
    """
    conditions = [
        (Project.name == project_name)
        & cast(IssueActivity.issue_number, String).in_(ids)
        for project_name, ids in filtered_issues.items()
        if ids
    ]
    if not conditions:
        return None
    combined = or_(*conditions) if len(conditions) > 1 else conditions[0]
    return ~combined


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
        self,
        limit: int = 20,
        filtered_issues: dict[str, list[str]] | None = None,
    ) -> list[CollectionRunSummary]:
        """Get the most recent collection runs with health statistics.

        Args:
            limit: Maximum number of runs to return.
            filtered_issues: Issue numbers per project to exclude from the
                displayed ``issues_collected`` count (e.g. Renovate/Dependabot
                "Dependency Dashboard" meta-issues), matching
                ``craft-dashboard.toml``'s ``[issues.filter]`` config. Without
                this, the count can show issues that are filtered out
                everywhere else in the UI, which is confusing (e.g. "1 issue
                collected" but clicking through shows nothing).

        """
        result = await self.session.execute(
            select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(limit)
        )
        runs = list(result.scalars())

        filtered_counts: dict[int, int] = {}
        run_ids = [run.id for run in runs]
        if run_ids and filtered_issues:
            excl = _build_excluded_issues_condition(filtered_issues)
            if excl is not None:
                # `excl` is a NOT(...) clause excluding filtered issues; negate
                # it to count only the filtered-out issues per run.
                count_query = (
                    select(Issue.collection_run_id, func.count())
                    .join(Project, Issue.project_id == Project.id)
                    .where(Issue.collection_run_id.in_(run_ids))
                    .where(~excl)
                    .group_by(Issue.collection_run_id)
                )
                count_rows = await self.session.execute(count_query)
                filtered_counts = {}
                for collection_run_id, filtered_count in count_rows.all():
                    filtered_counts[collection_run_id] = filtered_count

        return [
            {
                "id": run.id,
                "source": run.source,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "status": run.status,
                "projects_processed": run.projects_processed,
                "issues_collected": max(
                    run.issues_collected - filtered_counts.get(run.id, 0), 0
                ),
                "duration_seconds": run.duration_seconds,
                "errors": run.errors or [],
            }
            for run in runs
        ]

    async def get_recent_issue_activity(
        self,
        limit: int = 50,
        offset: int = 0,
        filtered_issues: dict[str, list[str]] | None = None,
    ) -> tuple[list[ActivityEntry], int]:
        """Return one page of recent issue/PR change events, newest first.

        Joined against ``Issue`` for the live GitHub URL; falls back to the
        title recorded at the time of the change if the issue row itself
        was later removed (e.g. project deletion). Issues listed in
        *filtered_issues* (e.g. Renovate/Dependabot "Dependency Dashboard"
        meta-issues) are excluded so they don't dominate the admin feed with
        noise.

        Returns a tuple of (page of entries, total matching count) so callers
        can render pagination controls.
        """
        query = (
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
        )
        excl = _build_excluded_activity_condition(filtered_issues or {})
        if excl is not None:
            query = query.where(excl)

        total_query = select(func.count()).select_from(query.subquery())
        total = (await self.session.execute(total_query)).scalar_one()

        rows = await self.session.execute(
            query.order_by(IssueActivity.occurred_at.desc()).limit(limit).offset(offset)
        )
        activity: list[ActivityEntry] = [
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
        return activity, total

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
        filtered_issues: dict[str, list[str]] | None = None,
    ) -> tuple[list[ActivityEntry], int]:
        """Return up to *limit* issues belonging to a collection run and the total count.

        Args:
            run_id: Primary key of the collection run.
            limit: Maximum number of issues to return.
            filtered_issues: Issue numbers per project to exclude (e.g.
                Renovate/Dependabot "Dependency Dashboard" meta-issues), matching
                ``craft-dashboard.toml``'s ``[issues.filter]`` config.

        Returns:
            A tuple of (issue list, total count).  The list is sorted by project
            name then issue title and capped at *limit* entries. Issues that
            weren't actually changed by this run (unchanged since last fetch)
            are included with change_type "unchanged".

        """
        excl = _build_excluded_issues_condition(filtered_issues or {})

        total_query = (
            select(func.count())
            .select_from(Issue)
            .join(Project, Issue.project_id == Project.id)
            .where(Issue.collection_run_id == run_id)
        )
        if excl is not None:
            total_query = total_query.where(excl)
        total_result = await self.session.execute(total_query)
        total = total_result.scalar_one()

        rows_query = (
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
        )
        if excl is not None:
            rows_query = rows_query.where(excl)
        rows_result = await self.session.execute(
            rows_query.order_by(Project.name, Issue.title).limit(limit)
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

    async def get_llm_service_status(self) -> LLMServiceStatus:
        """Derive the continuous `evaluate` worker's status from HTTP activity.

        "running" requires a `/next` poll within the stale window (the worker
        polls even when there's nothing to do, so this alone proves it's
        alive); "stalled_quota" means the worker self-reported a quota
        backoff that hasn't resumed yet (see ``report_quota_pause``) — this
        takes priority over plain "stalled" so an intentional, self-resolving
        pause isn't confused with a broken worker; "stalled" means it *has*
        called in before but not recently enough and isn't quota-paused;
        "unknown" means it has never been observed at all (e.g. right after
        a fresh deploy, before the worker's first poll).
        """
        last_poll_at, last_result_at = get_eval_activity()
        quota_resume_at = get_quota_pause_until()
        if quota_resume_at is not None:
            status = "stalled_quota"
        elif last_poll_at is None:
            status = "unknown"
        elif datetime.now(UTC) - last_poll_at <= _ACTIVITY_STALE_AFTER:
            status = "running"
        else:
            status = "stalled"

        if last_result_at is None:
            # `_last_result_submitted_at` is in-memory only and resets on
            # every app restart (frequent here — every push to `main`
            # redeploys). Fall back to the persisted last evaluation so the
            # admin page doesn't misleadingly show "Never" for a worker
            # that's been submitting results for weeks, just not since the
            # most recent restart. Excludes "pending" claim placeholders
            # (inserted by `/api/eval/next` when it locks a never-before-
            # evaluated issue) and rows with no summary yet — those aren't
            # completed results, just in-flight claims, and would otherwise
            # make this look more recent than the last real evaluation.
            last_result_at = _ensure_utc(
                await self.session.scalar(
                    select(func.max(LLMEvaluation.evaluated_at)).where(
                        LLMEvaluation.model_name != "pending",
                        LLMEvaluation.summary.is_not(None),
                        LLMEvaluation.summary != "",
                    )
                )
            )

        return {
            "status": status,
            "last_poll_at": last_poll_at,
            "last_result_at": last_result_at,
            "quota_resume_at": quota_resume_at,
        }

    async def get_recent_evaluations(
        self, limit: int = 20, offset: int = 0
    ) -> tuple[list[RecentEvaluationEntry], int]:
        """Return one page of recently submitted evaluations, newest first.

        Deliberately omits token/cost fields — the admin page doesn't surface
        OpenRouter spend.

        Returns a tuple of (page of entries, total matching count) so callers
        can render pagination controls.
        """
        base_query = (
            select(
                LLMEvaluation.issue_id,
                Project.name.label("project_name"),
                Issue.external_id,
                Issue.title,
                LLMEvaluation.model_name,
                LLMEvaluation.suggested_action,
                LLMEvaluation.evaluated_at,
            )
            .join(Issue, LLMEvaluation.issue_id == Issue.id)
            .join(Project, Issue.project_id == Project.id)
            .where(LLMEvaluation.latest.is_(True))
        )

        total_query = select(func.count()).select_from(base_query.subquery())
        total = (await self.session.execute(total_query)).scalar_one()

        result = await self.session.execute(
            base_query.order_by(LLMEvaluation.evaluated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        evaluations: list[RecentEvaluationEntry] = [
            {
                "issue_id": row.issue_id,
                "project": row.project_name,
                "external_id": row.external_id,
                "title": row.title,
                "model_name": row.model_name,
                "suggested_action": row.suggested_action,
                "evaluated_at": typing_cast("datetime", _ensure_utc(row.evaluated_at)),
            }
            for row in result
        ]
        return evaluations, total

    async def get_daily_evaluation_stats(
        self, days: int = 14
    ) -> list[DailyEvaluationCount]:
        """Return evaluation counts per day for the last `days` days.

        Counts every submitted evaluation (not just `latest=True` rows), so
        re-evaluations of the same issue each count toward the day they were
        submitted. Excludes `model_name == "pending"` claim placeholders
        (created by `/api/eval/next` when it locks a never-before-evaluated
        issue) — those aren't completed evaluations and would otherwise
        inflate a day's count for issues that are only *in flight*, not
        actually evaluated yet. No cost fields — the admin page doesn't
        surface OpenRouter spend.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        day = func.date(LLMEvaluation.evaluated_at)
        query = (
            select(day.label("day"), func.count(LLMEvaluation.id).label("total"))
            .where(
                LLMEvaluation.evaluated_at >= since,
                LLMEvaluation.model_name != "pending",
            )
            .group_by(day)
            .order_by(day)
        )
        result = await self.session.execute(query)
        return [{"date": str(row.day), "count": row.total} for row in result]

    async def get_queue_depth_history(self, hours: int = 48) -> list[QueueDepthPoint]:
        """Return recent eval queue-depth samples for the last `hours` hours.

        Samples are recorded as a side effect of `/api/eval/next` (see
        ``_maybe_record_queue_snapshot``), throttled to at most one every
        few minutes — so this reflects queue depth only while some worker
        (in-cluster or otherwise) has been actively polling.
        """
        since = datetime.now(UTC) - timedelta(hours=hours)
        query = (
            select(EvalQueueSnapshot)
            .where(EvalQueueSnapshot.captured_at >= since)
            .order_by(EvalQueueSnapshot.captured_at)
        )
        result = await self.session.execute(query)
        return [
            {
                "captured_at": typing_cast("datetime", _ensure_utc(row.captured_at)),
                "pending_count": row.pending_count,
                "total_open": row.total_open,
                "evaluated_today": row.evaluated_today,
            }
            for row in result.scalars()
        ]

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
