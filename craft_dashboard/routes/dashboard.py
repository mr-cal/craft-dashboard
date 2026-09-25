"""Dashboard overview routes."""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.services.dashboard_service import DashboardService

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["Dashboard"])


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the main operational dashboard overview.

    Shows operational KPI cards (PR velocity, 30d/365d throughput, untriaged backlog,
    all-time volume), 2x2 operational spotlights (least recent releases, aging contributor PRs,
    needs-triage queue, quick-wins radar), and the project health & quick-nav directory.
    """
    templates: Jinja2Templates = request.app.state.templates
    config = get_config(request)
    service = DashboardService(session)
    metrics = await service.get_homepage_metrics(config)

    # Legacy-compatible keys for backwards compatibility in test suites
    legacy_projects = (
        metrics["application_projects"]
        + metrics["library_projects"]
        + metrics["other_projects"]
    )

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "metrics": metrics,
            "project_count": metrics["project_count"],
            "open_issues": metrics["volume"]["open_issues"],
            "open_prs": metrics["volume"]["open_prs"],
            "projects": legacy_projects,
        },
    )
