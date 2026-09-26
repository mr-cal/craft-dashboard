"""Dashboard overview and cadence service queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypedDict

from sqlalchemy import func, or_, select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.repositories.issue_repository import (
    _build_excluded_issues_condition,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.config import DashboardConfig


class PRVelocity(TypedDict):
    """PR Velocity and response metrics for contributor and overall open PRs."""

    contributor_count: int
    contributor_avg_age: int | None
    contributor_avg_delta: int | None
    first_response_waiting_count: int
    first_response_avg_days: int | None
    first_response_avg_delta: int | None
    overall_count: int
    overall_avg_age: int | None
    overall_avg_delta: int | None


class ResolutionThroughput(TypedDict):
    """Closure throughput over 30 days and 365 days."""

    issues_30d: int
    prs_30d: int
    total_30d: int
    issues_365d: int
    prs_365d: int
    total_365d: int
    total_monthly_avg: int
    total_monthly_delta: int


class UntriagedBacklog(TypedDict):
    """Untriaged queues split for issues and pull requests."""

    issues_count: int
    issues_30d_new: int
    prs_count: int
    prs_30d_new: int


class VolumeStats(TypedDict):
    """All-time issues and PRs volume with trailing 30-day deltas."""

    open_issues: int
    closed_issues: int
    issues_30d_closed: int
    open_issues_30d: int
    open_prs: int
    closed_prs: int
    prs_30d_closed: int
    open_prs_30d: int
    total_items: int
    total_30d_closed: int
    open_total_30d: int


class AppReleaseSpotlight(TypedDict):
    """Application release status for least-recent release spotlight."""

    project_name: str
    version: str
    released_at: datetime | None
    days_ago: int | None
    badge_color: str
    is_fallback: bool


class AgingPR(TypedDict):
    """Aging open contributor PR item."""

    project_name: str
    external_id: str
    title: str
    author: str | None
    days_old: int
    url: str | None


class NeedsTriageIssue(TypedDict):
    """Needs triage issue item."""

    project_name: str
    external_id: str
    title: str
    author: str | None
    days_old: int
    url: str | None


class QuickWinIssue(TypedDict):
    """High-impact quick win issue item."""

    project_name: str
    external_id: str
    title: str
    quick_win: int
    impact: float | None
    complexity: float | None
    url: str | None


class ProjectHealthRow(TypedDict):
    """Project health metrics row with navigation shortcuts."""

    name: str
    github_org: str
    category: str
    open_issues: int
    open_prs: int
    show_prs: bool
    untriaged_count: int
    untriaged_pct: float
    triage_badge_color: str
    latest_release_version: str | None
    latest_release_days_ago: int | None
    release_badge_color: str
    show_release: bool


class RepoCadenceRow(TypedDict):
    """Cadence row for all tracked repositories."""

    name: str
    full_name: str
    github_org: str
    category: str
    latest_version: str
    released_at: datetime | None
    released_at_str: str
    days_ago: int | None
    badge_color: str
    commits_since: int | None
    is_fallback: bool


class HomepageMetrics(TypedDict):
    """Aggregated homepage metrics payload."""

    project_count: int
    velocity: PRVelocity
    throughput: ResolutionThroughput
    untriaged: UntriagedBacklog
    volume: VolumeStats
    least_recent_apps: list[AppReleaseSpotlight]
    aging_prs: list[AgingPR]
    needs_triage_issues: list[NeedsTriageIssue]
    quick_wins: list[QuickWinIssue]
    application_projects: list[ProjectHealthRow]
    library_projects: list[ProjectHealthRow]
    other_projects: list[ProjectHealthRow]


TRIAGE_GREEN_COUNT_THRESHOLD = 5
TRIAGE_BACKLOG_RED_CAP = 25
TRIAGE_RED_RATIO_THRESHOLD = 0.20
RELEASE_RED_DAYS_THRESHOLD = 60
RELEASE_YELLOW_DAYS_THRESHOLD = 30
QUICK_WIN_MIN_SCORE = 50


def compute_triage_badge_color(untriaged: int, total_open: int) -> str:
    """Return badge color for untriaged backlog.

    Rules:
    - If total_open <= 0 or untriaged <= 0: "neutral"
    - If untriaged < 5: "green" (projects with 1-4 untriaged issues are healthy)
    - If untriaged >= 25: "red" (unconditional backlog cap)
    - If ratio (untriaged / total_open) > 0.20: "red"
    - Otherwise: "yellow"
    """
    if total_open <= 0 or untriaged <= 0:
        return "neutral"
    if untriaged < TRIAGE_GREEN_COUNT_THRESHOLD:
        return "green"
    if untriaged >= TRIAGE_BACKLOG_RED_CAP:
        return "red"
    ratio = untriaged / total_open
    if ratio > TRIAGE_RED_RATIO_THRESHOLD:
        return "red"
    return "yellow"


def compute_release_badge_color(days_ago: int | None) -> str:
    """Return badge color for elapsed days since release."""
    if days_ago is None:
        return "neutral"
    if days_ago > RELEASE_RED_DAYS_THRESHOLD:
        return "red"
    if days_ago > RELEASE_YELLOW_DAYS_THRESHOLD:
        return "yellow"
    return "green"


def _parse_fallback_date(date_str: str | None) -> datetime | None:
    """Parse an ISO date string from configuration into a UTC datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _int_or_zero(val: object) -> int:
    """Safely convert a value to int, defaulting to 0."""
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return 0
    return 0


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
        if now is None:
            now = datetime.now(tz=UTC)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        thirty_days_ago = now - timedelta(days=30)
        one_year_ago = now - timedelta(days=365)
        excl = _build_excluded_issues_condition(config.filtered_issues)

        # 1. Project count
        project_count = (
            await self.session.scalar(
                select(func.count(Project.id)).where(Project.category != "aggregate")
            )
            or 0
        )

        # 2. PR Velocity and First Response
        pr_query = (
            select(
                Issue.id,
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
        contrib_ages: list[int] = []
        overall_ages: list[int] = []
        waiting_ages: list[int] = []

        for row in pr_rows:
            if row.created_at is not None:
                created = (
                    row.created_at
                    if row.created_at.tzinfo
                    else row.created_at.replace(tzinfo=UTC)
                )
                age_days = max(0, (now - created).days)
                overall_ages.append(age_days)
                if not row.author_is_maintainer and not row.author_is_bot:
                    contrib_ages.append(age_days)
                    comments = row.comments or []
                    has_maintainer_response = any(
                        isinstance(c, dict) and c.get("author") in maintainers_set
                        for c in comments
                    )
                    review_count = (
                        (row.metadata_ or {}).get("review_count", 0)
                        if isinstance(row.metadata_, dict)
                        else 0
                    )
                    if not has_maintainer_response and review_count == 0:
                        waiting_ages.append(age_days)

        velocity_contrib_avg = (
            int(round(sum(contrib_ages) / len(contrib_ages))) if contrib_ages else None
        )
        velocity_waiting_avg = (
            int(round(sum(waiting_ages) / len(waiting_ages))) if waiting_ages else None
        )
        velocity_overall_avg = (
            int(round(sum(overall_ages) / len(overall_ages))) if overall_ages else None
        )

        # Compute 12-month baseline of open PR age and response times across 12 monthly checkpoints
        pr_history_query = (
            select(
                Issue.id,
                Issue.created_at,
                Issue.closed_at,
                Issue.author_is_maintainer,
                Issue.author_is_bot,
                Issue.comments,
            )
            .join(Project, Issue.project_id == Project.id)
            .where(
                Issue.issue_type == "pull_request",
                Project.category != "aggregate",
                or_(
                    Issue.state == "open",
                    Issue.closed_at >= one_year_ago,
                ),
            )
        )
        if excl is not None:
            pr_history_query = pr_history_query.where(excl)
        pr_history_rows = (await self.session.execute(pr_history_query)).all()

        checkpoints = [now - timedelta(days=30 * i) for i in range(1, 13)]
        hist_contrib_avgs: list[float] = []
        hist_waiting_avgs: list[float] = []
        hist_overall_avgs: list[float] = []

        for cp in checkpoints:
            cp_contrib: list[int] = []
            cp_waiting: list[int] = []
            cp_overall: list[int] = []
            for prow in pr_history_rows:
                p_created = (
                    prow.created_at
                    if prow.created_at and prow.created_at.tzinfo
                    else prow.created_at.replace(tzinfo=UTC)
                    if prow.created_at
                    else None
                )
                p_closed = (
                    prow.closed_at
                    if prow.closed_at and prow.closed_at.tzinfo
                    else prow.closed_at.replace(tzinfo=UTC)
                    if prow.closed_at
                    else None
                )
                if (
                    p_created is not None
                    and p_created <= cp
                    and (p_closed is None or p_closed > cp)
                ):
                    age = max(0, (cp - p_created).days)
                    cp_overall.append(age)
                    if not prow.author_is_maintainer and not prow.author_is_bot:
                        cp_contrib.append(age)
                        comments = prow.comments or []
                        has_resp = False
                        for c in comments:
                            if (
                                isinstance(c, dict)
                                and c.get("author") in maintainers_set
                            ):
                                c_time = c.get("created_at")
                                try:
                                    if (
                                        c_time
                                        and datetime.fromisoformat(c_time).astimezone(
                                            UTC
                                        )
                                        <= cp
                                    ):
                                        has_resp = True
                                        break
                                except (ValueError, TypeError):
                                    has_resp = True
                                    break
                        if not has_resp:
                            cp_waiting.append(age)
            if cp_contrib:
                hist_contrib_avgs.append(sum(cp_contrib) / len(cp_contrib))
            if cp_waiting:
                hist_waiting_avgs.append(sum(cp_waiting) / len(cp_waiting))
            if cp_overall:
                hist_overall_avgs.append(sum(cp_overall) / len(cp_overall))

        contributor_12m_avg = (
            sum(hist_contrib_avgs) / len(hist_contrib_avgs)
            if hist_contrib_avgs
            else None
        )
        waiting_12m_avg = (
            sum(hist_waiting_avgs) / len(hist_waiting_avgs)
            if hist_waiting_avgs
            else None
        )
        overall_12m_avg = (
            sum(hist_overall_avgs) / len(hist_overall_avgs)
            if hist_overall_avgs
            else None
        )

        velocity: PRVelocity = {
            "contributor_count": len(contrib_ages),
            "contributor_avg_age": velocity_contrib_avg,
            "contributor_avg_delta": (
                velocity_contrib_avg - int(round(contributor_12m_avg))
                if velocity_contrib_avg is not None and contributor_12m_avg is not None
                else None
            ),
            "first_response_waiting_count": len(waiting_ages),
            "first_response_avg_days": velocity_waiting_avg,
            "first_response_avg_delta": (
                velocity_waiting_avg - int(round(waiting_12m_avg))
                if velocity_waiting_avg is not None and waiting_12m_avg is not None
                else None
            ),
            "overall_count": len(overall_ages),
            "overall_avg_age": velocity_overall_avg,
            "overall_avg_delta": (
                velocity_overall_avg - int(round(overall_12m_avg))
                if velocity_overall_avg is not None and overall_12m_avg is not None
                else None
            ),
        }

        # 3. Resolution Throughput
        tp_30_q = (
            select(Issue.issue_type, func.count(Issue.id))
            .join(Project, Issue.project_id == Project.id)
            .where(
                Issue.state.in_(["closed", "merged"]),
                Issue.closed_at >= thirty_days_ago,
                Project.category != "aggregate",
            )
        )
        if excl is not None:
            tp_30_q = tp_30_q.where(excl)
        tp_30_q = tp_30_q.group_by(Issue.issue_type)
        tp_30_rows = {
            row[0]: row[1] for row in (await self.session.execute(tp_30_q)).all()
        }

        tp_365_q = (
            select(Issue.issue_type, func.count(Issue.id))
            .join(Project, Issue.project_id == Project.id)
            .where(
                Issue.state.in_(["closed", "merged"]),
                Issue.closed_at >= one_year_ago,
                Project.category != "aggregate",
            )
        )
        if excl is not None:
            tp_365_q = tp_365_q.where(excl)
        tp_365_q = tp_365_q.group_by(Issue.issue_type)
        tp_365_rows = {
            row[0]: row[1] for row in (await self.session.execute(tp_365_q)).all()
        }

        issues_30d = tp_30_rows.get("issue", 0)
        prs_30d = tp_30_rows.get("pull_request", 0)
        issues_365d = tp_365_rows.get("issue", 0)
        prs_365d = tp_365_rows.get("pull_request", 0)

        total_30d = issues_30d + prs_30d
        total_365d = issues_365d + prs_365d
        total_monthly_avg = int(round(total_365d / 12)) if total_365d > 0 else 0
        total_monthly_delta = total_30d - total_monthly_avg

        throughput: ResolutionThroughput = {
            "issues_30d": issues_30d,
            "prs_30d": prs_30d,
            "total_30d": total_30d,
            "issues_365d": issues_365d,
            "prs_365d": prs_365d,
            "total_365d": total_365d,
            "total_monthly_avg": total_monthly_avg,
            "total_monthly_delta": total_monthly_delta,
        }

        # 4. Untriaged Queues with 30-day change
        # Open issues: suggested_action == 'needs_triage' or unevaluated
        untriaged_issues_base = (
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
                Issue.issue_type == "issue",
                Project.category != "aggregate",
                or_(
                    LLMEvaluation.suggested_action == "needs_triage",
                    LLMEvaluation.id.is_(None),
                    LLMEvaluation.suggested_action.is_(None),
                ),
            )
        )
        if excl is not None:
            untriaged_issues_base = untriaged_issues_base.where(excl)
        u_issues_res = (await self.session.execute(untriaged_issues_base)).one()

        # Open PRs: suggested_action == 'needs_review' or unevaluated
        untriaged_prs_base = (
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
                Issue.issue_type == "pull_request",
                Project.category != "aggregate",
                or_(
                    LLMEvaluation.suggested_action == "needs_review",
                    LLMEvaluation.id.is_(None),
                    LLMEvaluation.suggested_action.is_(None),
                ),
            )
        )
        if excl is not None:
            untriaged_prs_base = untriaged_prs_base.where(excl)
        u_prs_res = (await self.session.execute(untriaged_prs_base)).one()

        untriaged: UntriagedBacklog = {
            "issues_count": _int_or_zero(getattr(u_issues_res, "total", 0)),
            "issues_30d_new": _int_or_zero(getattr(u_issues_res, "new_30d", 0)),
            "prs_count": _int_or_zero(getattr(u_prs_res, "total", 0)),
            "prs_30d_new": _int_or_zero(getattr(u_prs_res, "new_30d", 0)),
        }

        # 5. All-Time Volume
        vol_q = (
            select(
                Issue.issue_type,
                Issue.state,
                func.count(Issue.id),
            )
            .join(Project, Issue.project_id == Project.id)
            .where(Project.category != "aggregate")
        )
        if excl is not None:
            vol_q = vol_q.where(excl)
        vol_q = vol_q.group_by(Issue.issue_type, Issue.state)
        vol_rows = (await self.session.execute(vol_q)).all()

        counts_map: dict[tuple[str, str], int] = {}
        for itype, st, cnt in vol_rows:
            counts_map[(itype, st)] = cnt

        open_issues = counts_map.get(("issue", "open"), 0)
        closed_issues = counts_map.get(("issue", "closed"), 0)
        open_prs = counts_map.get(("pull_request", "open"), 0)
        closed_prs = counts_map.get(("pull_request", "closed"), 0) + counts_map.get(
            ("pull_request", "merged"), 0
        )
        total_items = open_issues + closed_issues + open_prs + closed_prs
        total_30d_closed = issues_30d + prs_30d

        # Newly opened in the last 30 days
        open_30d_q = (
            select(
                Issue.issue_type,
                func.count(Issue.id),
            )
            .join(Project, Issue.project_id == Project.id)
            .where(
                Issue.state == "open",
                Issue.created_at >= thirty_days_ago,
                Project.category != "aggregate",
            )
        )
        if excl is not None:
            open_30d_q = open_30d_q.where(excl)
        open_30d_q = open_30d_q.group_by(Issue.issue_type)
        open_30d_rows = {
            row[0]: row[1] for row in (await self.session.execute(open_30d_q)).all()
        }
        open_issues_30d = open_30d_rows.get("issue", 0)
        open_prs_30d = open_30d_rows.get("pull_request", 0)
        open_total_30d = open_issues_30d + open_prs_30d

        volume: VolumeStats = {
            "open_issues": open_issues,
            "closed_issues": closed_issues,
            "issues_30d_closed": issues_30d,
            "open_issues_30d": open_issues_30d,
            "open_prs": open_prs,
            "closed_prs": closed_prs,
            "prs_30d_closed": prs_30d,
            "open_prs_30d": open_prs_30d,
            "total_items": total_items,
            "total_30d_closed": total_30d_closed,
            "open_total_30d": open_total_30d,
        }

        # 6. Releases across projects
        # Fetch latest release per project from DB
        rel_q = (
            select(
                Project.id.label("project_id"),
                Project.name.label("project_name"),
                Project.category,
                Project.github_org,
                Release.version,
                Release.released_at,
                Release.metadata_,
            )
            .join(Project, Release.project_id == Project.id)
            .where(Project.category != "aggregate")
            .order_by(Release.released_at.desc().nullslast())
        )
        rel_rows = (await self.session.execute(rel_q)).all()

        latest_rel_by_project: dict[int, Any] = {}
        for row in rel_rows:
            if row.project_id not in latest_rel_by_project:
                latest_rel_by_project[row.project_id] = row

        # Fetch all projects
        all_projects = (
            (
                await self.session.execute(
                    select(Project)
                    .where(Project.category != "aggregate")
                    .order_by(Project.display_order)
                )
            )
            .scalars()
            .all()
        )

        # Build AppReleaseSpotlight (Apps with least-recent releases)
        app_spotlights: list[AppReleaseSpotlight] = []
        for p in all_projects:
            if p.category == "application" and p.name not in config.hide_releases:
                rel = latest_rel_by_project.get(p.id)
                fallback_dt = _parse_fallback_date(
                    config.initial_release_dates.get(p.name)
                )
                if rel is not None and rel.released_at is not None:
                    rel_dt = (
                        rel.released_at
                        if rel.released_at.tzinfo
                        else rel.released_at.replace(tzinfo=UTC)
                    )
                    days_ago = max(0, (now - rel_dt).days)
                    app_spotlights.append(
                        {
                            "project_name": p.name,
                            "version": rel.version,
                            "released_at": rel.released_at,
                            "days_ago": days_ago,
                            "badge_color": compute_release_badge_color(days_ago),
                            "is_fallback": False,
                        }
                    )
                elif fallback_dt is not None:
                    days_ago = max(0, (now - fallback_dt).days)
                    version_tag = config.initial_release_tags.get(
                        p.name, "(unreleased)"
                    )
                    is_fallback = version_tag == "(unreleased)"
                    app_spotlights.append(
                        {
                            "project_name": p.name,
                            "version": version_tag,
                            "released_at": fallback_dt,
                            "days_ago": days_ago,
                            "badge_color": compute_release_badge_color(days_ago),
                            "is_fallback": is_fallback,
                        }
                    )
                else:
                    app_spotlights.append(
                        {
                            "project_name": p.name,
                            "version": "(unreleased)",
                            "released_at": None,
                            "days_ago": None,
                            "badge_color": "neutral",
                            "is_fallback": True,
                        }
                    )

        # Sort least recent apps: longest days ago first, None last
        app_spotlights.sort(
            key=lambda item: (item["days_ago"] is None, -(item["days_ago"] or 0))
        )

        # 7. Aging Contributor PRs (Top 5 oldest open contributor PRs)
        aging_pr_q = (
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
            aging_pr_q = aging_pr_q.where(excl)
        aging_pr_q = aging_pr_q.order_by(Issue.created_at.asc()).limit(5)
        aging_pr_rows = (await self.session.execute(aging_pr_q)).all()

        aging_prs: list[AgingPR] = []
        for row in aging_pr_rows:
            days_old = 0
            if row.created_at:
                c = (
                    row.created_at
                    if row.created_at.tzinfo
                    else row.created_at.replace(tzinfo=UTC)
                )
                days_old = max(0, (now - c).days)
            aging_prs.append(
                {
                    "project_name": row.project_name,
                    "external_id": row.external_id,
                    "title": row.title,
                    "author": row.author,
                    "days_old": days_old,
                    "url": row.url,
                }
            )

        # 8. Needs Triage (Top 5 oldest open issues needing assessment)
        needs_triage_q = (
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
                or_(
                    LLMEvaluation.suggested_action == "needs_triage",
                    LLMEvaluation.id.is_(None),
                    LLMEvaluation.suggested_action.is_(None),
                ),
            )
        )
        if excl is not None:
            needs_triage_q = needs_triage_q.where(excl)
        needs_triage_q = needs_triage_q.order_by(Issue.created_at.asc()).limit(5)
        needs_triage_rows = (await self.session.execute(needs_triage_q)).all()

        needs_triage_issues: list[NeedsTriageIssue] = []
        for row in needs_triage_rows:
            days_old = 0
            if row.created_at:
                c = (
                    row.created_at
                    if row.created_at.tzinfo
                    else row.created_at.replace(tzinfo=UTC)
                )
                days_old = max(0, (now - c).days)
            needs_triage_issues.append(
                {
                    "project_name": row.project_name,
                    "external_id": row.external_id,
                    "title": row.title,
                    "author": row.author,
                    "days_old": days_old,
                    "url": row.url,
                }
            )

        # 9. Quick-Wins Radar (Top 5 high impact/low complexity issues)
        quick_wins_q = (
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
            quick_wins_q = quick_wins_q.where(excl)
        quick_wins_rows = (await self.session.execute(quick_wins_q)).all()

        quick_win_candidates: list[QuickWinIssue] = []
        for row in quick_wins_rows:
            scores = row.scores or {}
            qw_raw = scores.get("quick_win")
            if qw_raw is not None:
                try:
                    qw_val = int(round(float(qw_raw)))
                except (ValueError, TypeError):
                    qw_val = 0
                if qw_val >= QUICK_WIN_MIN_SCORE:
                    quick_win_candidates.append(
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
        quick_win_candidates.sort(key=lambda x: x["quick_win"], reverse=True)
        quick_wins = quick_win_candidates[:5]

        # 10. Combined Project Health & Quick-Nav Directory
        # Per project stats: open issues, open PRs, untriaged items
        open_items_q = (
            select(
                Project.id,
                Issue.issue_type,
                LLMEvaluation.suggested_action,
                func.count(Issue.id),
            )
            .join(Project, Issue.project_id == Project.id)
            .outerjoin(
                LLMEvaluation,
                (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest,
            )
            .where(Issue.state == "open", Project.category != "aggregate")
        )
        if excl is not None:
            open_items_q = open_items_q.where(excl)
        open_items_q = open_items_q.group_by(
            Project.id, Issue.issue_type, LLMEvaluation.suggested_action
        )
        open_items_rows = (await self.session.execute(open_items_q)).all()

        # Aggregate counts per project
        project_open_issues: dict[int, int] = {}
        project_open_prs: dict[int, int] = {}
        project_untriaged: dict[int, int] = {}

        for pid, itype, action, cnt in open_items_rows:
            if itype == "issue":
                project_open_issues[pid] = project_open_issues.get(pid, 0) + cnt
                if action == "needs_triage" or action is None:
                    project_untriaged[pid] = project_untriaged.get(pid, 0) + cnt
            elif itype == "pull_request":
                project_open_prs[pid] = project_open_prs.get(pid, 0) + cnt
                if action == "needs_review" or action is None:
                    project_untriaged[pid] = project_untriaged.get(pid, 0) + cnt

        application_projects: list[ProjectHealthRow] = []
        library_projects: list[ProjectHealthRow] = []
        other_projects: list[ProjectHealthRow] = []

        for p in all_projects:
            op_issues = project_open_issues.get(p.id, 0)
            op_prs = project_open_prs.get(p.id, 0)
            u_count = project_untriaged.get(p.id, 0)
            tot_open = op_issues + op_prs
            u_pct = round((u_count / tot_open) * 100.0, 1) if tot_open > 0 else 0.0

            rel = latest_rel_by_project.get(p.id)
            fallback_dt = _parse_fallback_date(config.initial_release_dates.get(p.name))

            rel_ver = None
            days_ago = None
            if rel is not None and rel.released_at is not None:
                rel_ver = rel.version
                r_dt = (
                    rel.released_at
                    if rel.released_at.tzinfo
                    else rel.released_at.replace(tzinfo=UTC)
                )
                days_ago = max(0, (now - r_dt).days)
            elif fallback_dt is not None:
                rel_ver = config.initial_release_tags.get(p.name, "(unreleased)")
                days_ago = max(0, (now - fallback_dt).days)

            row_data: ProjectHealthRow = {
                "name": p.name,
                "github_org": p.github_org or "canonical",
                "category": p.category,
                "open_issues": op_issues,
                "open_prs": op_prs,
                "show_prs": p.name not in config.hide_prs,
                "untriaged_count": u_count,
                "untriaged_pct": u_pct,
                "triage_badge_color": compute_triage_badge_color(u_count, tot_open),
                "latest_release_version": rel_ver,
                "latest_release_days_ago": days_ago,
                "release_badge_color": compute_release_badge_color(days_ago),
                "show_release": p.name not in config.hide_releases,
            }

            cat = p.category
            if config.craft_applications and p.name in config.craft_applications:
                cat = "application"
            elif config.craft_libraries and p.name in config.craft_libraries:
                cat = "library"
            elif (
                config.craft_other
                and p.name in config.craft_other
                or p.name == "snapcraft (launchpad)"
            ):
                cat = "other"

            if cat == "application":
                application_projects.append(row_data)
            elif cat == "library":
                library_projects.append(row_data)
            else:
                other_projects.append(row_data)

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
            "application_projects": application_projects,
            "library_projects": library_projects,
            "other_projects": other_projects,
        }

    async def get_all_repos_release_cadence(
        self,
        config: DashboardConfig,
        now: datetime | None = None,
    ) -> list[RepoCadenceRow]:
        """Compute release cadence for all tracked projects, sorted by least-recent release."""
        if now is None:
            now = datetime.now(tz=UTC)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        # Get latest release per project
        rel_q = (
            select(
                Project.id.label("project_id"),
                Project.name.label("project_name"),
                Project.category,
                Project.github_org,
                Release.version,
                Release.released_at,
                Release.metadata_,
            )
            .join(Project, Release.project_id == Project.id)
            .where(Project.category != "aggregate")
            .order_by(Release.released_at.desc().nullslast())
        )
        rel_exec = await self.session.execute(rel_q)
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
            fallback_dt = _parse_fallback_date(config.initial_release_dates.get(p.name))

            cat = p.category
            if config.craft_applications and p.name in config.craft_applications:
                cat = "application"
            elif config.craft_libraries and p.name in config.craft_libraries:
                cat = "library"
            elif (
                config.craft_other
                and p.name in config.craft_other
                or p.name == "snapcraft (launchpad)"
            ):
                cat = "other"

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
