"""Dashboard overview routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project

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
    project_count = await session.scalar(select(func.count(Project.id)))
    open_issues = await session.scalar(
        select(func.count(Issue.id)).where(
            Issue.state == "open", Issue.issue_type == "issue"
        )
    )
    open_prs = await session.scalar(
        select(func.count(Issue.id)).where(
            Issue.state == "open", Issue.issue_type == "pull_request"
        )
    )

    # Per-project summary
    project_stats = await session.execute(
        select(
            Project.name,
            Project.category,
            func.count(Issue.id)
            .filter(Issue.state == "open", Issue.issue_type == "issue")
            .label("open_issues"),
            func.count(Issue.id)
            .filter(Issue.state == "open", Issue.issue_type == "pull_request")
            .label("open_prs"),
        )
        .outerjoin(Issue, Issue.project_id == Project.id)
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
            "projects": projects,
        },
    )
