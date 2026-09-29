"""Dashboard overview and cadence service queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.repositories.issue_filters import (
    build_excluded_issues_condition,
)
from craft_dashboard.services.dashboard.badges import (
    RELEASE_RED_DAYS_THRESHOLD,
    RELEASE_YELLOW_DAYS_THRESHOLD,
    TRIAGE_BACKLOG_RED_CAP,
    TRIAGE_GREEN_COUNT_THRESHOLD,
    TRIAGE_RED_RATIO_THRESHOLD,
    compute_release_badge_color,
    compute_triage_badge_color,
)
from craft_dashboard.services.dashboard.health import (
    aggregate_open_counts,
    build_open_items_query,
    build_project_health_rows,
    fetch_project_open_counts,
    untriaged_percent,
)
from craft_dashboard.services.dashboard.projects import (
    fetch_tracked_projects,
    resolve_project_category,
)
from craft_dashboard.services.dashboard.releases import (
    build_app_spotlights,
    build_latest_release_query,
    fetch_latest_release_by_project,
    parse_fallback_date,
)
from craft_dashboard.services.dashboard.spotlights import (
    QUICK_WIN_MIN_SCORE,
    compute_aging_prs,
    compute_needs_triage_issues,
    compute_quick_wins,
)
from craft_dashboard.services.dashboard.throughput import (
    compute_resolution_throughput,
)
from craft_dashboard.services.dashboard.types import (
    AgingPR,
    AppReleaseSpotlight,
    ContributorAwaitingPR,
    HomepageMetrics,
    NeedsTriageIssue,
    ProjectHealthRow,
    ProjectTriageHealthRow,
    PRVelocity,
    QuickWinIssue,
    RepoCadenceRow,
    ResolutionThroughput,
    TriageResponsivenessData,
    UntriagedBacklog,
    VolumeStats,
)
from craft_dashboard.services.dashboard.untriaged import compute_untriaged_backlog
from craft_dashboard.services.dashboard.velocity import (
    OpenPRAgeBuckets,
    build_velocity,
    compute_pr_velocity,
    compute_velocity_baselines,
    fetch_pr_history_rows,
    iter_open_pr_ages,
)
from craft_dashboard.services.dashboard.volume import compute_volume

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.config import DashboardConfig

__all__ = [
    "AgingPR",
    "AppReleaseSpotlight",
    "ContributorAwaitingPR",
    "DashboardService",
    "HomepageMetrics",
    "NeedsTriageIssue",
    "PRVelocity",
    "ProjectHealthRow",
    "ProjectTriageHealthRow",
    "QUICK_WIN_MIN_SCORE",
    "QuickWinIssue",
    "RELEASE_RED_DAYS_THRESHOLD",
    "RELEASE_YELLOW_DAYS_THRESHOLD",
    "RepoCadenceRow",
    "ResolutionThroughput",
    "TRIAGE_BACKLOG_RED_CAP",
    "TRIAGE_GREEN_COUNT_THRESHOLD",
    "TRIAGE_RED_RATIO_THRESHOLD",
    "TriageResponsivenessData",
    "UntriagedBacklog",
    "VolumeStats",
    "compute_release_badge_color",
    "compute_triage_badge_color",
]


def _normalize_now(now: datetime | None) -> datetime:
    """Return a UTC-aware "now", defaulting to the current time."""
    if now is None:
        return datetime.now(tz=UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now


class DashboardService:
    """Service for computing dashboard KPI metrics, spotlights, and release cadence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_homepage_metrics(
        self,
        config: DashboardConfig,
        now: datetime | None = None,
    ) -> HomepageMetrics:
        """Compute all aggregated metrics for the homepage."""
        now = _normalize_now(now)
        thirty_days_ago = now - timedelta(days=30)
        one_year_ago = now - timedelta(days=365)
        excl = build_excluded_issues_condition(config.filtered_issues)
        maintainers_set = set(config.maintainers + config.launchpad_maintainers)

        project_count = (
            await self.session.scalar(
                select(func.count(Project.id)).where(Project.category != "aggregate")
            )
            or 0
        )

        velocity = await compute_pr_velocity(
            self.session, maintainers_set, excl, now, one_year_ago
        )
        throughput = await compute_resolution_throughput(
            self.session, excl, thirty_days_ago, one_year_ago
        )
        untriaged = await compute_untriaged_backlog(self.session, excl, thirty_days_ago)
        volume = await compute_volume(
            self.session,
            excl,
            thirty_days_ago,
            (throughput["issues_30d"], throughput["prs_30d"]),
        )

        latest_rel_by_project = await fetch_latest_release_by_project(self.session)
        all_projects = await fetch_tracked_projects(self.session)
        app_spotlights = build_app_spotlights(
            all_projects, latest_rel_by_project, config, now
        )

        aging_prs = await compute_aging_prs(self.session, excl, now)
        needs_triage_issues = await compute_needs_triage_issues(self.session, excl, now)
        quick_wins = await compute_quick_wins(self.session, excl)

        open_counts = await fetch_project_open_counts(self.session, excl)
        health_rows = build_project_health_rows(
            all_projects, open_counts, latest_rel_by_project, config, now
        )

        return {
            "project_count": project_count,
            "velocity": velocity,
            "throughput": throughput,
            "untriaged": untriaged,
            "volume": volume,
            "least_recent_apps": app_spotlights,
            "aging_prs": aging_prs,
            "needs_triage_issues": needs_triage_issues,
            "quick_wins": quick_wins,
            "application_projects": health_rows["application"],
            "library_projects": health_rows["library"],
            "other_projects": health_rows["other"],
        }

    async def get_all_repos_release_cadence(
        self,
        config: DashboardConfig,
        now: datetime | None = None,
    ) -> list[RepoCadenceRow]:
        """Compute release cadence for all tracked projects, sorted by least-recent release."""
        now = _normalize_now(now)

        rel_exec = await self.session.execute(build_latest_release_query())
        rel_rows = rel_exec.all() if hasattr(rel_exec, "all") else list(rel_exec)

        latest_rel_by_project: dict[int, Any] = {}
        for row in rel_rows:
            if (
                hasattr(row, "project_id")
                and row.project_id not in latest_rel_by_project
            ):
                latest_rel_by_project[row.project_id] = row

        proj_exec = await self.session.execute(
            select(Project)
            .where(Project.category != "aggregate")
            .order_by(Project.display_order)
        )
        if hasattr(proj_exec, "scalars"):
            all_projects = proj_exec.scalars().all()
        elif hasattr(proj_exec, "all"):
            all_projects = proj_exec.all()
        else:
            all_projects = list(proj_exec)

        category_labels = {
            "application": "Application",
            "library": "Library",
            "other": "Other",
        }

        cadence_rows: list[RepoCadenceRow] = []
        for p in all_projects:
            if p.name in config.hide_releases:
                continue

            rel = latest_rel_by_project.get(p.id)
            fallback_dt = parse_fallback_date(config.initial_release_dates.get(p.name))
            cat = resolve_project_category(p, config)

            version = "(unreleased)"
            released_at = None
            released_at_str = "—"
            days_ago = None
            commits_since = None
            is_fallback = False

            if rel is not None and rel.released_at is not None:
                version = rel.version
                released_at = rel.released_at
                r_dt = (
                    rel.released_at
                    if rel.released_at.tzinfo
                    else rel.released_at.replace(tzinfo=UTC)
                )
                days_ago = max(0, (now - r_dt).days)
                released_at_str = r_dt.strftime("%Y-%m-%d")
                if rel.metadata_ and isinstance(rel.metadata_, dict):
                    commits_since = rel.metadata_.get("commits_since_tag")
            elif fallback_dt is not None:
                tag = config.initial_release_tags.get(p.name)
                if tag and tag != "(unreleased)":
                    version = tag
                    is_fallback = False
                else:
                    version = "(unreleased)"
                    is_fallback = True
                released_at = fallback_dt
                days_ago = max(0, (now - fallback_dt).days)
                released_at_str = fallback_dt.strftime("%Y-%m-%d")

            cadence_rows.append(
                {
                    "name": p.name,
                    "full_name": f"{p.github_org or 'canonical'}/{p.name}",
                    "github_org": p.github_org or "canonical",
                    "category": category_labels.get(cat, cat.title()),
                    "latest_version": version,
                    "released_at": released_at,
                    "released_at_str": released_at_str,
                    "days_ago": days_ago,
                    "badge_color": compute_release_badge_color(days_ago),
                    "commits_since": commits_since,
                    "is_fallback": is_fallback,
                }
            )

        # Sort: longest days ago first (None last)
        cadence_rows.sort(
            key=lambda item: (item["days_ago"] is None, -(item["days_ago"] or 0))
        )
        return cadence_rows

    async def _fetch_triage_velocity(
        self,
        config: DashboardConfig,
        now: datetime,
        excl: ColumnElement[bool] | None,
    ) -> tuple[PRVelocity, list[ContributorAwaitingPR]]:
        """Compute PR velocity and the contributor PRs awaiting a first response."""
        one_year_ago = now - timedelta(days=365)
        pr_query = (
            select(
                Issue.id,
                Issue.external_id,
                Issue.title,
                Issue.author,
                Issue.url,
                Project.name.label("project_name"),
                Issue.created_at,
                Issue.author_is_maintainer,
                Issue.author_is_bot,
                Issue.metadata_,
                Issue.comments,
            )
            .join(Project, Issue.project_id == Project.id)
            .where(
                Issue.state == "open",
                Issue.issue_type == "pull_request",
                Project.category != "aggregate",
            )
        )
        if excl is not None:
            pr_query = pr_query.where(excl)
        pr_rows = (await self.session.execute(pr_query)).all()

        maintainers_set = set(config.maintainers + config.launchpad_maintainers)
        buckets = OpenPRAgeBuckets()
        awaiting_prs: list[ContributorAwaitingPR] = []

        for row, info in iter_open_pr_ages(pr_rows, now, maintainers_set):
            buckets.overall.append(info.age_days)
            if not info.is_contributor:
                continue
            buckets.contributor.append(info.age_days)
            if info.is_waiting:
                buckets.waiting.append(info.age_days)
                awaiting_prs.append(
                    {
                        "project_name": row.project_name,
                        "external_id": row.external_id,
                        "title": row.title,
                        "author": row.author,
                        "days_waiting": info.age_days,
                        "url": row.url,
                        "created_at_str": info.created.strftime("%Y-%m-%d"),
                    }
                )

        awaiting_prs.sort(key=lambda x: x["days_waiting"], reverse=True)

        history_rows = await fetch_pr_history_rows(self.session, excl, one_year_ago)
        baselines = compute_velocity_baselines(history_rows, now, maintainers_set)
        return build_velocity(buckets, baselines), awaiting_prs

    async def _fetch_triage_coverage(
        self,
        excl: ColumnElement[bool] | None,
    ) -> tuple[int, int, dict[str, int]]:
        """Return open item totals, evaluated counts, and suggested action counts."""
        total_open_q = (
            select(func.count(Issue.id))
            .join(Project, Issue.project_id == Project.id)
            .where(Issue.state == "open", Project.category != "aggregate")
        )
        if excl is not None:
            total_open_q = total_open_q.where(excl)
        total_open = (await self.session.execute(total_open_q)).scalar() or 0

        evaluated_q = (
            select(func.count(func.distinct(LLMEvaluation.issue_id)))
            .select_from(LLMEvaluation)
            .join(Issue, LLMEvaluation.issue_id == Issue.id)
            .join(Project, Issue.project_id == Project.id)
            .where(
                Issue.state == "open",
                Project.category != "aggregate",
                LLMEvaluation.latest,
            )
        )
        if excl is not None:
            evaluated_q = evaluated_q.where(excl)
        evaluated_count = (await self.session.execute(evaluated_q)).scalar() or 0

        action_q = (
            select(
                LLMEvaluation.suggested_action,
                func.count().label("action_count"),
            )
            .join(Issue, LLMEvaluation.issue_id == Issue.id)
            .join(Project, Issue.project_id == Project.id)
            .where(
                Issue.state == "open",
                Project.category != "aggregate",
                LLMEvaluation.latest,
            )
            .group_by(LLMEvaluation.suggested_action)
        )
        if excl is not None:
            action_q = action_q.where(excl)
        action_rows = (await self.session.execute(action_q)).all()
        action_counts: dict[str, int] = {
            str(row[0]): int(row[1]) for row in action_rows if row[0] is not None
        }
        return total_open, evaluated_count, action_counts

    async def get_triage_and_responsiveness_data(
        self,
        config: DashboardConfig,
        now: datetime | None = None,
    ) -> TriageResponsivenessData:
        """Fetch metrics for the Triage and Responsiveness dashboard."""
        now = now or datetime.now(tz=UTC)
        excl = build_excluded_issues_condition(config.filtered_issues)

        velocity, awaiting_prs = await self._fetch_triage_velocity(config, now, excl)

        all_projects_q = (
            select(Project)
            .where(Project.category != "aggregate")
            .order_by(Project.display_order, Project.name)
        )
        all_projects = (await self.session.execute(all_projects_q)).scalars().all()

        open_items_rows = (
            await self.session.execute(build_open_items_query(excl))
        ).all()
        counts = aggregate_open_counts(open_items_rows)

        application_projects: list[ProjectTriageHealthRow] = []
        library_projects: list[ProjectTriageHealthRow] = []
        other_projects: list[ProjectTriageHealthRow] = []

        healthy_count = 0
        attention_count = 0
        overdue_count = 0

        for p in all_projects:
            op_issues = counts.open_issues.get(p.id, 0)
            op_prs = counts.open_prs.get(p.id, 0)
            u_issues = counts.untriaged_issues.get(p.id, 0)
            u_prs = counts.untriaged_prs.get(p.id, 0)
            u_total = u_issues + u_prs
            tot_open = op_issues + op_prs
            badge_color = compute_triage_badge_color(u_total, tot_open)

            if badge_color == "green":
                healthy_count += 1
            elif badge_color == "yellow":
                attention_count += 1
            elif badge_color == "red":
                overdue_count += 1

            row_data: ProjectTriageHealthRow = {
                "name": p.name,
                "github_org": p.github_org or "canonical",
                "category": p.category,
                "open_issues": op_issues,
                "open_prs": op_prs,
                "untriaged_issues": u_issues,
                "untriaged_prs": u_prs,
                "untriaged_total": u_total,
                "untriaged_pct": untriaged_percent(u_total, tot_open),
                "badge_color": badge_color,
            }

            if p.category == "application":
                application_projects.append(row_data)
            elif p.category == "library":
                library_projects.append(row_data)
            else:
                other_projects.append(row_data)

        total_open, evaluated_count, action_counts = await self._fetch_triage_coverage(
            excl
        )

        return TriageResponsivenessData(
            velocity=velocity,
            awaiting_prs=awaiting_prs,
            application_projects=application_projects,
            library_projects=library_projects,
            other_projects=other_projects,
            action_counts=action_counts,
            total_open=total_open,
            evaluated_count=evaluated_count,
            healthy_project_count=healthy_count,
            attention_project_count=attention_count,
            overdue_project_count=overdue_count,
        )
