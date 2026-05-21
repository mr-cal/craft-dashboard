"""Stats routes for dependencies, releases, and trends."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.dependency import Dependency
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.models.snapshot import Snapshot

router = APIRouter(prefix="/stats")


@router.get("", response_class=RedirectResponse)
async def stats_index() -> RedirectResponse:
    """Redirect /stats to /stats/trends."""
    return RedirectResponse(url="/stats/trends", status_code=302)


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
    """Render the releases table showing the latest release per project+branch."""
    templates: Jinja2Templates = request.app.state.templates

    from datetime import UTC, datetime

    from sqlalchemy import and_

    # Subquery: latest release per project+branch
    latest_sub = (
        select(
            Release.project_id,
            Release.branch,
            func.max(Release.released_at).label("max_released_at"),
        )
        .group_by(Release.project_id, Release.branch)
        .subquery()
    )

    result = await session.execute(
        select(
            Project.name.label("project_name"),
            Release.branch,
            Release.version,
            Release.released_at,
            Release.metadata_,
        )
        .join(Project, Release.project_id == Project.id)
        .join(
            latest_sub,
            and_(
                Release.project_id == latest_sub.c.project_id,
                Release.branch == latest_sub.c.branch,
                Release.released_at == latest_sub.c.max_released_at,
            ),
        )
        .where(Project.category == "application")
        .order_by(Project.display_order, Release.branch)
    )

    releases = []
    for row in result:
        days_ago = None
        if row.released_at:
            released = row.released_at.replace(tzinfo=UTC) if row.released_at.tzinfo is None else row.released_at
            days_ago = (datetime.now(tz=UTC) - released).days
        commits_since_tag = None
        if row.metadata_:
            commits_since_tag = row.metadata_.get("commits_since_tag")
        releases.append({
            "project_name": row.project_name,
            "branch": row.branch,
            "version": row.version,
            "released_at": row.released_at,
            "days_ago": days_ago,
            "commits_since_tag": commits_since_tag,
        })

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


@router.get("/trends/all-data", response_class=JSONResponse)
async def trends_all_data(
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Return snapshot trend data for all projects as JSON for Chart.js."""
    result = await session.execute(
        select(
            Project.name.label("project_name"),
            Snapshot.snapshot_date,
            Snapshot.open_issues,
            Snapshot.open_prs,
            Snapshot.open_bugs,
        )
        .join(Project, Snapshot.project_id == Project.id)
        .order_by(Project.display_order, Snapshot.snapshot_date)
    )

    projects: dict[str, dict] = {}
    for row in result:
        name = row.project_name
        if name not in projects:
            projects[name] = {"dates": [], "open_issues": [], "open_prs": [], "open_bugs": []}
        projects[name]["dates"].append(row.snapshot_date.isoformat())
        projects[name]["open_issues"].append(row.open_issues)
        projects[name]["open_prs"].append(row.open_prs)
        projects[name]["open_bugs"].append(row.open_bugs)

    project_order = list(projects.keys())
    return JSONResponse({"projects": projects, "order": project_order})


async def _get_trend_chart_data(session: AsyncSession, project: str) -> dict:
    """Fetch snapshot trend data for a project and return Chart.js-compatible dict."""
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

    dates: list[str] = []
    open_issues: list[int] = []
    open_prs: list[int] = []
    open_bugs: list[int] = []

    for row in result:
        dates.append(row.snapshot_date.isoformat())
        open_issues.append(row.open_issues)
        open_prs.append(row.open_prs)
        open_bugs.append(row.open_bugs)

    return {
        "labels": dates,
        "datasets": [
            {"label": "Open Issues", "data": open_issues, "borderColor": "#4e79a7"},
            {"label": "Open PRs", "data": open_prs, "borderColor": "#f28e2b"},
            {"label": "Open Bugs", "data": open_bugs, "borderColor": "#e15759"},
        ],
    }


@router.get("/trends/data", response_class=JSONResponse)
async def trends_data(
    session: AsyncSession = Depends(get_db_session),
    project: str = Query(...),
) -> JSONResponse:
    """Return trend data as JSON for Chart.js (API endpoint)."""
    data = await _get_trend_chart_data(session, project)
    return JSONResponse(data)


@router.get("/trends/chart", response_class=HTMLResponse)
async def trends_chart_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query(...),
) -> HTMLResponse:
    """Return an HTML partial that renders the trend chart for a given project.

    Called by HTMX when the project selector changes or the page loads.
    Embeds chart data as inline JS so Chart.js can render without a second request.
    """
    templates: Jinja2Templates = request.app.state.templates
    import json  # noqa: PLC0415

    chart_data = await _get_trend_chart_data(session, project)

    return templates.TemplateResponse(
        request,
        "stats/partials/trend_chart.html",
        {"chart_data_json": json.dumps(chart_data), "project": project},
    )
