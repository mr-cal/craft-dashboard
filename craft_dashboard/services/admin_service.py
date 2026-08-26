"""Admin service queries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypedDict
from typing import cast as typing_cast

from sqlalchemy import String, cast, func, literal, or_, select
from sqlalchemy.orm import aliased

from craft_dashboard.collectors.github import GitHubCollector, RateLimitStatus
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.models.commit_scan_run import CommitScanRun
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


class ProjectRefreshEntry(TypedDict):
    """Per-project rotation entry shown on the admin dashboard's Refresh Schedule tab.

    Ordered by ``last_refreshed_at`` ascending (never-refreshed first) —
    this ordering *is* the hourly rotation queue (see
    ``craft_dashboard.collectors.scheduler.get_least_recently_refreshed``),
    so the first entry is always "up next".
    """

    project: str
    source: str
    last_refreshed_at: datetime | None
    consecutive_failures: int
    days_since_last: int | None
    last_error: str | None
    # Tracked separately from the full-refresh columns above: the frequent
    # (every 10 minutes) open-issue-only poll fails far more often on large
    # repos, mostly on transient/self-healing network errors, and shouldn't
    # be conflated with full-refresh health (see
    # ``craft_dashboard.collectors.scheduler.record_refresh_error``).
    open_poll_consecutive_failures: int
    open_poll_last_error: str | None


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
    # Hourly full-project refresh rotation (see
    # ``craft_dashboard.collectors.scheduler.get_least_recently_refreshed``):
    # the most recently completed full refresh, the most overdue one (i.e.
    # "up next" in the rotation), and how many projects currently have a
    # failing full refresh, so the Overview tab surfaces rotation health
    # without needing to open the Refresh Schedule tab.
    most_recent_full_refresh: datetime | None
    most_overdue_full_refresh: datetime | None
    full_refresh_failing_count: int


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
    url: str | None
    issue_type: str
    state: str
    model_name: str
    suggested_action: str | None
    evaluated_at: datetime


class DailyEvaluationCount(TypedDict):
    """Count of evaluations submitted on a given calendar day."""

    date: str
    count: int


class OutdatedEvaluationCounts(TypedDict):
    """Breakdown of open issues needing (re-)evaluation, by reason.

    Mirrors the "is_up_to_date" condition in
    ``build_pending_evaluation_query``/``_maybe_record_queue_snapshot``, split
    into mutually exclusive reasons so the LLM Evaluations tab can show *why*
    issues need (re-)evaluation, not just how many:

    - ``never_evaluated``: no evaluation exists at all yet.
    - ``version_outdated``: an evaluation exists but used an older
      ``CURRENT_EVAL_VERSION``.
    - ``content_changed``: an evaluation exists at the current eval version,
      but the issue's content has changed since (``content_hash`` mismatch).
    """

    never_evaluated: int
    version_outdated: int
    content_changed: int


class QueueDepthPoint(TypedDict):
    """A single sampled point of eval queue depth, for charting over time."""

    captured_at: datetime
    pending_count: int
    total_open: int
    evaluated_today: int


class CommitScanHistoryPoint(TypedDict):
    """Daily commit-scan invalidation totals by signal."""

    day: str
    qualified_ref: int
    path: int
    semantic: int
    bare_ref: int
    launchpad: int


class CommitScanSummary(TypedDict):
    """Rolling invalidation headline and warning state."""

    rolling_total: int
    warn_threshold: int | None
    warn: bool


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

    _SIGNAL_COLUMNS = (
        CommitScanRun.invalidated_qualified_ref,
        CommitScanRun.invalidated_path,
        CommitScanRun.invalidated_semantic,
        CommitScanRun.invalidated_bare_ref,
        CommitScanRun.invalidated_launchpad,
    )

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

    async def get_project_refresh_list(self) -> list[ProjectRefreshEntry]:
        """Get the per-project rotation order for the Refresh Schedule tab.

        This mirrors the ordering used by
        ``craft_dashboard.collectors.scheduler.get_least_recently_refreshed``
        (least-recently-refreshed first, never-refreshed first of all) so the
        admin page shows exactly which project/source pair the hourly
        rotation cron will pick up next. Includes both GitHub and Launchpad
        sources — the old GitHub-only filter is gone now that Launchpad also
        participates in ``RefreshSchedule``.
        """
        result = await self.session.execute(
            select(
                Project.name,
                RefreshSchedule.source,
                RefreshSchedule.last_refreshed_at,
                RefreshSchedule.consecutive_failures,
                RefreshSchedule.last_error,
                RefreshSchedule.open_poll_consecutive_failures,
                RefreshSchedule.open_poll_last_error,
            )
            .join(RefreshSchedule, RefreshSchedule.project_id == Project.id)
            .where(Project.category != "aggregate")
            .order_by(
                RefreshSchedule.last_refreshed_at.asc().nullsfirst(),
                Project.name,
            )
        )
        return [
            {
                "project": row.name,
                "source": row.source,
                "last_refreshed_at": _ensure_utc(row.last_refreshed_at),
                "consecutive_failures": row.consecutive_failures,
                "days_since_last": _days_delta(_ensure_utc(row.last_refreshed_at)),
                "last_error": row.last_error,
                "open_poll_consecutive_failures": row.open_poll_consecutive_failures,
                "open_poll_last_error": row.open_poll_last_error,
            }
            for row in result
        ]

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
        most_recent_full_refresh = await self.session.scalar(
            select(func.max(RefreshSchedule.last_refreshed_at))
        )
        most_overdue_full_refresh = await self.session.scalar(
            select(func.min(RefreshSchedule.last_refreshed_at))
        )
        full_refresh_failing_count = await self.session.scalar(
            select(func.count())
            .select_from(RefreshSchedule)
            .where(RefreshSchedule.consecutive_failures > 0)
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
            "most_recent_full_refresh": _ensure_utc(most_recent_full_refresh),
            "most_overdue_full_refresh": _ensure_utc(most_overdue_full_refresh),
            "full_refresh_failing_count": full_refresh_failing_count or 0,
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
                Issue.url,
                Issue.issue_type,
                Issue.state,
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
                "url": row.url,
                "issue_type": row.issue_type,
                "state": row.state,
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

    async def get_commit_scan_history(
        self, days: int = 14
    ) -> list[CommitScanHistoryPoint]:
        """Return daily commit-scanner invalidation totals, broken out by signal."""
        since = datetime.now(UTC) - timedelta(days=days)
        day = func.date(CommitScanRun.scanned_at).label("day")
        query = (
            select(
                day,
                func.sum(CommitScanRun.invalidated_qualified_ref).label(
                    "qualified_ref"
                ),
                func.sum(CommitScanRun.invalidated_path).label("path"),
                func.sum(CommitScanRun.invalidated_semantic).label("semantic"),
                func.sum(CommitScanRun.invalidated_bare_ref).label("bare_ref"),
                func.sum(CommitScanRun.invalidated_launchpad).label("launchpad"),
            )
            .where(
                CommitScanRun.scanned_at >= since,
                CommitScanRun.dry_run.is_(False),
            )
            .group_by(day)
            .order_by(day)
        )
        result = await self.session.execute(query)
        return [
            {
                "day": str(row.day),
                "qualified_ref": row.qualified_ref or 0,
                "path": row.path or 0,
                "semantic": row.semantic or 0,
                "bare_ref": row.bare_ref or 0,
                "launchpad": row.launchpad or 0,
            }
            for row in result
        ]

    async def get_commit_scan_summary(
        self, days: int = 7, warn_threshold: int | None = None
    ) -> CommitScanSummary:
        """Return rolling invalidation totals and warning state."""
        since = datetime.now(UTC) - timedelta(days=days)
        total_expr = sum(
            (func.coalesce(func.sum(column), 0) for column in self._SIGNAL_COLUMNS),
            literal(0),
        )
        query = select(total_expr.label("rolling_total")).where(
            CommitScanRun.scanned_at >= since,
            CommitScanRun.dry_run.is_(False),
        )
        rolling_total = (await self.session.execute(query)).scalar_one()
        return {
            "rolling_total": rolling_total,
            "warn_threshold": warn_threshold,
            "warn": warn_threshold is not None and rolling_total > warn_threshold,
        }

    async def get_outdated_evaluation_counts(
        self, filtered_issues: dict[str, list[str]] | None = None
    ) -> OutdatedEvaluationCounts:
        """Return open-issue counts needing (re-)evaluation, bucketed by reason.

        Mirrors the priority tiers in
        ``craft_dashboard.llm.evaluation_queue.build_pending_evaluation_query``.
        """
        latest_evaluation = aliased(LLMEvaluation)
        never_evaluated = latest_evaluation.id.is_(None)
        version_outdated = latest_evaluation.id.is_not(None) & (
            latest_evaluation.eval_version.is_(None)
            | (latest_evaluation.eval_version != CURRENT_EVAL_VERSION)
        )
        content_changed = (
            latest_evaluation.id.is_not(None)
            & (latest_evaluation.eval_version == CURRENT_EVAL_VERSION)
            & Issue.content_hash.is_distinct_from(latest_evaluation.issue_data_hash)
        )

        query = (
            select(
                func.count().filter(never_evaluated),
                func.count().filter(version_outdated),
                func.count().filter(content_changed),
            )
            .select_from(Issue)
            .join(Project, Issue.project_id == Project.id)
            .outerjoin(
                latest_evaluation,
                (latest_evaluation.issue_id == Issue.id) & latest_evaluation.latest,
            )
            .where(Issue.state == "open")
            .where(Project.category != "aggregate")
        )
        excl = _build_excluded_issues_condition(filtered_issues or {})
        if excl is not None:
            query = query.where(excl)

        row = (await self.session.execute(query)).one()
        return {
            "never_evaluated": row[0] or 0,
            "version_outdated": row[1] or 0,
            "content_changed": row[2] or 0,
        }


def _days_delta(ts: datetime | None) -> int | None:
    """Return whole calendar days between *ts*'s date and today's date (UTC).

    Uses calendar-date subtraction rather than `timedelta.days` on the raw
    duration: the latter floors toward negative infinity, so e.g. a
    timestamp 30 minutes in the past yields a duration of
    `timedelta(days=-1, seconds=86370)`, whose `.days` is `-1` — rendering
    "1 day ago" for something that happened moments ago. Comparing calendar
    dates instead gives `0` for "today", `1` for "yesterday", etc.
    """
    if ts is None:
        return None
    return (datetime.now(UTC).date() - ts.astimezone(UTC).date()).days
