"""Stats routes for dependencies, releases, and trends."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.dependency import Dependency
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.models.snapshot import Snapshot

router = APIRouter(prefix="/stats")


@router.get("", response_class=RedirectResponse)
async def stats_index() -> RedirectResponse:
    """Redirect /stats to /stats/dependencies."""
    return RedirectResponse(url="/stats/dependencies", status_code=302)


@router.get("/dependencies", response_class=HTMLResponse)
async def dependencies_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the dependency usage table."""
    templates: Jinja2Templates = request.app.state.templates

    result = await session.execute(
        select(
            Dependency.dependency_name,
            Dependency.branch,
            Dependency.version_spec,
            Project.name.label("project_name"),
        )
        .join(Project, Dependency.project_id == Project.id)
        .order_by(Project.display_order, Dependency.branch, Dependency.dependency_name)
    )

    dependencies = [
        {
            "project_name": row.project_name,
            "branch": row.branch,
            "dependency_name": row.dependency_name,
            "version_spec": row.version_spec,
        }
        for row in result
    ]

    return templates.TemplateResponse(
        request,
        "stats/dependencies.html",
        {"dependencies": dependencies},
    )


@router.get("/releases", response_class=HTMLResponse)
async def releases_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the releases table."""
    templates: Jinja2Templates = request.app.state.templates

    result = await session.execute(
        select(
            Release.version,
            Release.branch,
            Release.released_at,
            Project.name.label("project_name"),
        )
        .join(Project, Release.project_id == Project.id)
        .order_by(Release.released_at.desc().nullslast())
    )

    releases = [
        {
            "project_name": row.project_name,
            "version": row.version,
            "branch": row.branch,
            "released_at": row.released_at,
        }
        for row in result
    ]

    return templates.TemplateResponse(
        request,
        "stats/releases.html",
        {"releases": releases},
    )


@router.get("/trends", response_class=HTMLResponse)
async def trends_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the trends page with Chart.js graphs."""
    templates: Jinja2Templates = request.app.state.templates

    project_result = await session.execute(
        select(Project.name).order_by(Project.display_order)
    )
    project_names = [row.name for row in project_result]

    return templates.TemplateResponse(
        request,
        "stats/trends.html",
        {"project_names": project_names},
    )


@router.get("/trends/data", response_class=JSONResponse)
async def trends_data(
    session: AsyncSession = Depends(get_db_session),
    project: str = Query(...),
) -> JSONResponse:
    """Return trend data as JSON for Chart.js."""
    result = await session.execute(
        select(
            Snapshot.snapshot_date,
            Snapshot.open_issues,
            Snapshot.open_prs,
            Snapshot.open_bugs,
        )
        .join(Project, Snapshot.project_id == Project.id)
        .where(Project.name == project)
        .order_by(Snapshot.snapshot_date)
    )

    dates = []
    open_issues = []
    open_prs = []
    open_bugs = []

    for row in result:
        dates.append(row.snapshot_date.isoformat())
        open_issues.append(row.open_issues)
        open_prs.append(row.open_prs)
        open_bugs.append(row.open_bugs)

    return JSONResponse(
        {
            "labels": dates,
            "datasets": [
                {
                    "label": "Open Issues",
                    "data": open_issues,
                    "borderColor": "#4e79a7",
                },
                {"label": "Open PRs", "data": open_prs, "borderColor": "#f28e2b"},
                {
                    "label": "Open Bugs",
                    "data": open_bugs,
                    "borderColor": "#e15759",
                },
            ],
        }
    )
