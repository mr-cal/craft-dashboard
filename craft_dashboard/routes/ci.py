"""CI failure catalog routes: the error signature catalog and drill-down."""

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.models.views import CIFilters
from craft_dashboard.services.ci_service import CIService

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

    from craft_dashboard.config import DashboardConfig


router = APIRouter(prefix="/stats/ci", tags=["Stats"])

VALID_BRANCH_CLASSES = {"main", "pr", "scheduled", "other"}
VALID_TEST_TYPES = {"pytest", "spread", "go", "linter", "infra", "job"}


def _build_filters(
    project: str,
    workflow: str,
    branch: str,
    test_type: str,
    search: str,
    sort: str,
    page: int,
) -> CIFilters:
    return CIFilters(
        project=project,
        workflow=workflow if project else "",
        branch_class=branch if branch in VALID_BRANCH_CLASSES else "",
        test_type=test_type if test_type in VALID_TEST_TYPES else "",
        search=search,
        sort_by=sort,
        page=page,
    )


async def _build_catalog_context(
    session: AsyncSession,
    *,
    filters: CIFilters,
    config: "DashboardConfig",
) -> dict[str, Any]:
    service = CIService(session)
    result = await service.get_catalog(filters, config)
    project_names = await service.get_project_names()
    workflow_names = (
        await service.get_workflow_names(filters.project) if filters.project else []
    )

    return {
        "rows": result.rows,
        "total_count": result.total_count,
        "total_pages": result.total_pages,
        "page": result.page,
        "project_names": project_names,
        "workflow_names": workflow_names,
        "filter_project": filters.project,
        "filter_workflow": filters.workflow,
        "filter_branch": filters.branch_class,
        "filter_type": filters.test_type,
        "filter_search": filters.search,
        "sort_by": filters.sort_by,
    }


@router.get("", response_class=HTMLResponse)
async def ci_catalog(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    workflow: str = Query("", alias="workflow"),
    branch: str = Query("", alias="branch"),
    test_type: str = Query("", alias="type"),
    search: str = Query("", alias="search"),
    sort: str = Query("fail_rate", alias="sort"),
    page: int = Query(1, ge=1),
) -> HTMLResponse:
    """Render the CI failure catalog page."""
    templates: Jinja2Templates = request.app.state.templates
    filters = _build_filters(project, workflow, branch, test_type, search, sort, page)
    context = await _build_catalog_context(
        session, filters=filters, config=get_config(request)
    )
    return templates.TemplateResponse(request, "stats/ci.html", context)


@router.get("/table", response_class=HTMLResponse)
async def ci_catalog_table(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    workflow: str = Query("", alias="workflow"),
    branch: str = Query("", alias="branch"),
    test_type: str = Query("", alias="type"),
    search: str = Query("", alias="search"),
    sort: str = Query("fail_rate", alias="sort"),
    page: int = Query(1, ge=1),
) -> HTMLResponse:
    """Return just the CI failure catalog table partial (for HTMX swapping)."""
    templates: Jinja2Templates = request.app.state.templates
    filters = _build_filters(project, workflow, branch, test_type, search, sort, page)
    context = await _build_catalog_context(
        session, filters=filters, config=get_config(request)
    )
    return templates.TemplateResponse(request, "stats/partials/ci_table.html", context)


@router.get("/signature", response_class=HTMLResponse)
async def ci_signature_detail(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query(..., alias="project"),
    workflow: str = Query(..., alias="workflow"),
    job: str = Query(..., alias="job"),
    variant: str = Query("", alias="variant"),
    test_type: str = Query(..., alias="type"),
    signature: str = Query(..., alias="signature"),
    branch: str = Query("", alias="branch"),
) -> HTMLResponse:
    """Render the drill-down trend view for one failure signature.

    Identified by the (project, workflow, job, variant, test_type, signature)
    tuple rather than a surrogate ID: signatures aren't stored in their own
    table (see ``models/ci_failure.py``), so this is the natural key.
    """
    templates: Jinja2Templates = request.app.state.templates
    if branch and branch not in VALID_BRANCH_CLASSES:
        raise HTTPException(status_code=400, detail="Invalid branch filter")

    service = CIService(session)
    detail = await service.get_signature_detail(
        project_name=project,
        workflow_name=workflow,
        job_name=job,
        job_variant=variant or None,
        test_type=test_type,
        signature=signature,
        branch_class=branch,
        config=get_config(request),
    )

    chart_data = {
        "labels": [point.day.isoformat() for point in detail.daily_trend],
        "datasets": [
            {
                "label": "Fail rate",
                "data": [
                    (point.fail_count / point.total_count) if point.total_count else 0
                    for point in detail.daily_trend
                ],
            }
        ],
    }

    return templates.TemplateResponse(
        request,
        "stats/ci_signature.html",
        {
            "detail": detail,
            "project": project,
            "workflow": workflow,
            "filter_branch": branch,
            "chart_data": chart_data,
        },
    )
