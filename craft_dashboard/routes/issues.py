"""Issue and PR triage routes."""

from dataclasses import asdict
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.views import IssueFilters, IssueView
from craft_dashboard.repositories.issue_repository import IssueRepository

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/issues")

VALID_PER_PAGE = {100, 250, 1000}
PER_PAGE_ALL = 0  # sentinel for "show all"
DEFAULT_PER_PAGE = 100

ALL_SCORES = {
    "staleness": "Staleness",
    "duplicateness": "Duplicateness",
    "complexity": "Complexity",
    "support_request": "Support Request",
    "readiness": "Readiness",
}
# Scores where a higher value is better (green) rather than worse (red)
INVERTED_SCORES: frozenset[str] = frozenset({"readiness"})
DEFAULT_SCORES = "staleness,readiness"


class IssueTemplateContext(TypedDict):
    """Template context used by issue list and partial responses."""

    issues: list[IssueView]
    project_names: list[str]
    filter_project: str
    filter_source: str
    filter_state: str
    filter_type: str
    filter_action: str
    filter_author_role: str
    filter_search: str
    sort_by: str
    page: int
    total_pages: int
    per_page: int
    filter_scores: str
    active_scores: list[str]
    all_scores: dict[str, str]
    inverted_scores: frozenset[str]
    filter_llm_status: str
    total_count: int


def _normalize_per_page(per_page: int) -> int:
    """Normalize requested page size to supported values."""
    if per_page in VALID_PER_PAGE or per_page == PER_PAGE_ALL:
        return per_page
    return DEFAULT_PER_PAGE


def _parse_per_page(value: str) -> int:
    """Parse per_page query string, returning DEFAULT_PER_PAGE for invalid values."""
    if not value:
        return DEFAULT_PER_PAGE
    try:
        return int(value)
    except ValueError:
        return DEFAULT_PER_PAGE


def _build_issue_filters(
    *,
    project: str,
    source: str,
    state: str,
    issue_type: str,
    action: str,
    author_role: str,
    sort: str,
    page: int,
    search: str,
    per_page: int,
    llm_status: str,
) -> IssueFilters:
    """Build normalized issue filters from route query parameters."""
    return IssueFilters(
        project=project,
        source=source,
        state=state,
        issue_type=issue_type,
        action=action,
        author_role=author_role,
        sort_by=sort,
        page=page,
        search=search,
        items_per_page=_normalize_per_page(per_page),
        llm_status=llm_status,
    )


async def _build_issue_context(
    session: AsyncSession,
    *,
    filters: IssueFilters,
    scores: str,
) -> IssueTemplateContext:
    """Build the template context for issue list rendering."""
    repo = IssueRepository(session)
    result = await repo.search(filters)
    project_names = await repo.get_project_names()

    normalized_scores = scores.strip()
    active_scores: list[str] = [
        score_name.strip()
        for score_name in scores.split(",")
        if score_name.strip() in ALL_SCORES
    ]
    if not normalized_scores:
        active_scores = []
    elif not active_scores:
        active_scores = cast(list[str], DEFAULT_SCORES.split(","))

    context: IssueTemplateContext = {
        "issues": result.issues,
        "project_names": project_names,
        "filter_project": filters.project,
        "filter_source": filters.source,
        "filter_state": filters.state,
        "filter_type": filters.issue_type,
        "filter_action": filters.action,
        "filter_author_role": filters.author_role,
        "filter_search": filters.search,
        "sort_by": filters.sort_by,
        "page": result.page,
        "total_pages": result.total_pages,
        "per_page": filters.items_per_page,
        "filter_scores": scores,
        "active_scores": active_scores,
        "all_scores": ALL_SCORES,
        "inverted_scores": INVERTED_SCORES,
        "filter_llm_status": filters.llm_status,
        "total_count": result.total_count,
    }
    return context


class IssueSort(StrEnum):
    """Valid sort fields for the issue list."""

    staleness = "staleness"
    duplicateness = "duplicateness"
    complexity = "complexity"
    support_request = "support_request"
    readiness = "readiness"
    age = "age"
    updated = "updated"
    title = "title"
    author = "author"
    number = "number"


def _build_original_issue_url(issue: dict[str, Any]) -> str:
    """Build the upstream issue URL from issue data."""
    if issue["source"] == "launchpad":
        return (
            f"https://bugs.launchpad.net/{issue['project_name']}/+bug/"
            f"{issue['external_id']}"
        )
    return (
        f"https://github.com/canonical/{issue['project_name']}/issues/"
        f"{issue['external_id']}"
    )


@router.get("", response_class=HTMLResponse)
async def issue_list(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    source: str = Query("", alias="source"),
    state: str = Query("open", alias="state"),
    issue_type: str = Query("", alias="type"),
    action: str = Query("", alias="action"),
    author_role: str = Query("", alias="author_role"),
    sort: str = Query("staleness", alias="sort"),
    page: int = Query(1, ge=1),
    search: str = Query("", alias="search"),
    per_page: str = Query("", alias="per_page"),
    scores: str = Query(DEFAULT_SCORES, alias="scores"),
    llm_status: str = Query("", alias="llm_status"),
) -> HTMLResponse:
    """Render the issue triage list page."""
    templates: Jinja2Templates = request.app.state.templates
    effective_per_page = _normalize_per_page(_parse_per_page(per_page))

    filters = IssueFilters(
        project=project,
        source=source,
        state=state,
        issue_type=issue_type,
        action=action,
        author_role=author_role,
        sort_by=sort,
        page=page,
        search=search,
        items_per_page=effective_per_page,
        llm_status=llm_status,
    )
    context = await _build_issue_context(session, filters=filters, scores=scores)

    return templates.TemplateResponse(
        request,
        "issues/list.html",
        cast(dict[str, Any], dict(context)),
    )


@router.get("/table", response_class=HTMLResponse)
async def issue_table_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    source: str = Query("", alias="source"),
    state: str = Query("open", alias="state"),
    issue_type: str = Query("", alias="type"),
    action: str = Query("", alias="action"),
    author_role: str = Query("", alias="author_role"),
    sort: str = Query("staleness", alias="sort"),
    page: int = Query(1, ge=1),
    search: str = Query("", alias="search"),
    per_page: str = Query("", alias="per_page"),
    scores: str = Query(DEFAULT_SCORES, alias="scores"),
    llm_status: str = Query("", alias="llm_status"),
) -> HTMLResponse:
    """Return just the issue table partial (for HTMX swapping)."""
    templates: Jinja2Templates = request.app.state.templates
    effective_per_page = _normalize_per_page(_parse_per_page(per_page))

    filters = IssueFilters(
        project=project,
        source=source,
        state=state,
        issue_type=issue_type,
        action=action,
        author_role=author_role,
        sort_by=sort,
        page=page,
        search=search,
        items_per_page=effective_per_page,
        llm_status=llm_status,
    )
    context = await _build_issue_context(session, filters=filters, scores=scores)

    return templates.TemplateResponse(
        request,
        "issues/partials/issue_table.html",
        cast(dict[str, Any], dict(context)),
    )


@router.get("/{project}/{number}", response_class=HTMLResponse)
async def issue_detail(
    request: Request,
    project: str,
    number: str,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the issue detail page with evaluation history."""
    templates: Jinja2Templates = request.app.state.templates
    repo = IssueRepository(session)
    issue = await repo.get_issue_detail(project, number)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    evaluation_history = cast(list[dict[str, Any]], issue["evaluation_history"])
    current_evaluation = evaluation_history[0] if evaluation_history else None

    return templates.TemplateResponse(
        request,
        "issues/detail.html",
        {
            "issue": issue,
            "current_evaluation": current_evaluation,
            "evaluation_history": evaluation_history,
            "original_issue_url": _build_original_issue_url(issue),
        },
    )


@router.get("/export")
async def issue_export(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    source: str = Query("", alias="source"),
    state: str = Query("open", alias="state"),
    issue_type: str = Query("", alias="type"),
    action: str = Query("", alias="action"),
    author_role: str = Query("", alias="author_role"),
    sort: str = Query("staleness", alias="sort"),
    page: int = Query(1, ge=1),
    search: str = Query("", alias="search"),
    per_page: str = Query("", alias="per_page"),
    scores: str = Query(DEFAULT_SCORES, alias="scores"),
    llm_status: str = Query("", alias="llm_status"),
) -> JSONResponse:
    """Export all matching issues as JSON."""
    del request, scores, per_page

    filters = _build_issue_filters(
        project=project,
        source=source,
        state=state,
        issue_type=issue_type,
        action=action,
        author_role=author_role,
        sort=sort,
        page=page,
        search=search,
        per_page=PER_PAGE_ALL,
        llm_status=llm_status,
    )
    repo = IssueRepository(session)
    result = await repo.search(filters)
    payload = jsonable_encoder([asdict(issue) for issue in result.issues])

    return JSONResponse(
        payload,
        headers={"Content-Disposition": 'attachment; filename="issues-export.json"'},
    )
