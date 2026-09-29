"""Per-project open item counts and homepage project health rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.services.dashboard.badges import (
    compute_release_badge_color,
    compute_triage_badge_color,
)
from craft_dashboard.services.dashboard.projects import resolve_project_category
from craft_dashboard.services.dashboard.releases import resolve_release_summary

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy import ColumnElement, Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.config import DashboardConfig
    from craft_dashboard.services.dashboard.types import ProjectHealthRow


@dataclass
class ProjectOpenCounts:
    """Open and untriaged item counts keyed by project id."""

    open_issues: dict[int, int] = field(default_factory=dict)
    open_prs: dict[int, int] = field(default_factory=dict)
    untriaged_issues: dict[int, int] = field(default_factory=dict)
    untriaged_prs: dict[int, int] = field(default_factory=dict)

    def untriaged_total(self, project_id: int) -> int:
        """Return untriaged issues plus PRs for a project."""
        return self.untriaged_issues.get(project_id, 0) + self.untriaged_prs.get(
            project_id, 0
        )


def build_open_items_query(excl: ColumnElement[bool] | None) -> Select[Any]:
    """Return the per-project open item count query grouped by triage action."""
    query = (
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
        query = query.where(excl)
    return query.group_by(Project.id, Issue.issue_type, LLMEvaluation.suggested_action)


def aggregate_open_counts(rows: Sequence[Any]) -> ProjectOpenCounts:
    """Fold grouped open item rows into per-project counters."""
    counts = ProjectOpenCounts()
    for pid, itype, action, cnt in rows:
        if itype == "issue":
            counts.open_issues[pid] = counts.open_issues.get(pid, 0) + cnt
            if action == "needs_triage" or action is None:
                counts.untriaged_issues[pid] = counts.untriaged_issues.get(pid, 0) + cnt
        elif itype == "pull_request":
            counts.open_prs[pid] = counts.open_prs.get(pid, 0) + cnt
            if action == "needs_review" or action is None:
                counts.untriaged_prs[pid] = counts.untriaged_prs.get(pid, 0) + cnt
    return counts


async def fetch_project_open_counts(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
) -> ProjectOpenCounts:
    """Fetch and aggregate open item counts for every tracked project."""
    rows = (await session.execute(build_open_items_query(excl))).all()
    return aggregate_open_counts(rows)


def untriaged_percent(untriaged: int, total_open: int) -> float:
    """Return the untriaged share of open items as a rounded percentage."""
    return round((untriaged / total_open) * 100.0, 1) if total_open > 0 else 0.0


def _health_row(
    project: Project,
    counts: ProjectOpenCounts,
    release: tuple[str | None, int | None],
    config: DashboardConfig,
) -> ProjectHealthRow:
    """Build one homepage project health row."""
    op_issues = counts.open_issues.get(project.id, 0)
    op_prs = counts.open_prs.get(project.id, 0)
    u_count = counts.untriaged_total(project.id)
    tot_open = op_issues + op_prs
    rel_ver, days_ago = release

    return {
        "name": project.name,
        "github_org": project.github_org or "canonical",
        "category": project.category,
        "open_issues": op_issues,
        "open_prs": op_prs,
        "show_prs": project.name not in config.hide_prs,
        "untriaged_count": u_count,
        "untriaged_pct": untriaged_percent(u_count, tot_open),
        "triage_badge_color": compute_triage_badge_color(u_count, tot_open),
        "latest_release_version": rel_ver,
        "latest_release_days_ago": days_ago,
        "release_badge_color": compute_release_badge_color(days_ago),
        "show_release": project.name not in config.hide_releases,
    }


def build_project_health_rows(
    projects: Sequence[Project],
    counts: ProjectOpenCounts,
    latest_rel_by_project: dict[int, Any],
    config: DashboardConfig,
    now: datetime,
) -> dict[str, list[ProjectHealthRow]]:
    """Build homepage health rows bucketed by application, library, and other."""
    buckets: dict[str, list[ProjectHealthRow]] = {
        "application": [],
        "library": [],
        "other": [],
    }
    for p in projects:
        release = resolve_release_summary(
            p, latest_rel_by_project.get(p.id), config, now
        )
        row_data = _health_row(p, counts, release, config)
        category = resolve_project_category(p, config)
        buckets.get(category, buckets["other"]).append(row_data)
    return buckets
