"""Dashboard overview routes."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.models.snapshot import Snapshot
from craft_dashboard.repositories.issue_repository import (
    _build_excluded_issues_condition,
)

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the main dashboard overview.

    Shows summary stats: total projects, open issues, open PRs,
    and per-project breakdown.
    """
    templates: Jinja2Templates = request.app.state.templates

    # Summary counts
    excl = _build_excluded_issues_condition(get_config(request).filtered_issues)
    project_count = await session.scalar(
        select(func.count(Project.id)).where(Project.category != "aggregate")
    )
    open_issues_q = (
        select(func.count(Issue.id))
        .join(Project, Issue.project_id == Project.id)
        .where(Issue.state == "open", Issue.issue_type == "issue")
    )
    open_prs_q = (
        select(func.count(Issue.id))
        .join(Project, Issue.project_id == Project.id)
        .where(Issue.state == "open", Issue.issue_type == "pull_request")
    )
    if excl is not None:
        open_issues_q = open_issues_q.where(excl)
        open_prs_q = open_prs_q.where(excl)
    open_issues = await session.scalar(open_issues_q)
    open_prs = await session.scalar(open_prs_q)

    # Get the most recent aggregate snapshot
    latest_snap = await session.execute(
        select(Snapshot)
        .join(Project, Snapshot.project_id == Project.id)
        .where(Project.category == "aggregate")
        .order_by(Snapshot.snapshot_date.desc())
        .limit(1)
    )
    latest = latest_snap.scalar()

    # Get the snapshot from ~30 days ago
    thirty_days_ago = datetime.now(tz=UTC) - timedelta(days=30)
    old_snap = await session.execute(
        select(Snapshot)
        .join(Project, Snapshot.project_id == Project.id)
        .where(Project.category == "aggregate")
        .where(Snapshot.snapshot_date <= thirty_days_ago)
        .order_by(Snapshot.snapshot_date.desc())
        .limit(1)
    )
    old = old_snap.scalar()

    # Calculate changes
    if latest and old:
        issue_change = (latest.open_issues or 0) - (old.open_issues or 0)
        pr_change = (latest.open_prs or 0) - (old.open_prs or 0)
    else:
        issue_change = None
        pr_change = None

    # Per-project summary
    open_issue_filter = [Issue.state == "open", Issue.issue_type == "issue"]
    open_pr_filter = [Issue.state == "open", Issue.issue_type == "pull_request"]
    if excl is not None:
        open_issue_filter.append(excl)
        open_pr_filter.append(excl)
    project_stats = await session.execute(
        select(
            Project.name,
            Project.category,
            func.count(Issue.id).filter(*open_issue_filter).label("open_issues"),
            func.count(Issue.id).filter(*open_pr_filter).label("open_prs"),
        )
        .outerjoin(Issue, Issue.project_id == Project.id)
        .where(Project.category != "aggregate")
        .group_by(Project.id)
        .order_by(Project.display_order)
    )

    projects = [
        {
            "name": row.name,
            "category": row.category,
            "open_issues": row.open_issues,
            "open_prs": row.open_prs,
        }
        for row in project_stats
    ]

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "project_count": project_count or 0,
            "open_issues": open_issues or 0,
            "open_prs": open_prs or 0,
            "issue_change": issue_change,
            "pr_change": pr_change,
            "projects": projects,
        },
    )
