"""Issue and PR triage routes."""

from datetime import UTC, datetime
from enum import StrEnum

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project

router = APIRouter(prefix="/issues")

VALID_PER_PAGE = {10, 50, 1000}
DEFAULT_PER_PAGE = 50


class IssueSort(StrEnum):
    """Valid sort fields for the issue list."""

    staleness = "staleness"
    age = "age"
    updated = "updated"
    title = "title"
    author = "author"
    number = "number"


_VALID_SORT_FIELDS = {e.value for e in IssueSort}


def _compute_age_days(created_at: datetime | None) -> int:
    """Compute days since creation."""
    if created_at is None:
        return 0
    now = datetime.now(tz=UTC)
    created = (
        created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at
    )
    return (now - created).days


def _apply_author_role_filter(query, author_role: str):  # noqa: ANN001
    """Apply author role filtering to the query."""
    if not author_role:
        return query

    role_list = [r.strip() for r in author_role.split(",") if r.strip()]
    role_conditions = []
    for role in role_list:
        if role == "maintainer":
            role_conditions.append(
                (Issue.author_is_maintainer.is_(True))
                & (Issue.author_is_bot.is_(False))
            )
        elif role == "contributor":
            role_conditions.append(
                (Issue.author_is_maintainer.is_(False))
                & (Issue.author_is_bot.is_(False))
            )
        elif role == "bot":
            role_conditions.append(Issue.author_is_bot.is_(True))
    if len(role_conditions) == 1:
        query = query.where(role_conditions[0])
    elif role_conditions:
        query = query.where(or_(*role_conditions))
    return query


async def _query_issues(
    session: AsyncSession,
    *,
    project: str = "",
    source: str = "",
    issue_type: str = "",
    action: str = "",
    author_role: str = "",
    sort_by: str = "staleness",
    page: int = 1,
    bots: list[str] | None = None,
    search: str = "",
    items_per_page: int = DEFAULT_PER_PAGE,
) -> tuple[list[dict], int]:
    """Query issues with filters and return (issues, total_pages)."""
    query = (
        select(
            Issue,
            Project.name.label("project_name"),
            LLMEvaluation.summary,
            LLMEvaluation.suggested_action,
            LLMEvaluation.scores,
        )
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest.is_(True),
        )
        .where(Issue.state == "open")
    )

    if project:
        project_list = [p.strip() for p in project.split(",") if p.strip()]
        if len(project_list) == 1:
            query = query.where(Project.name == project_list[0])
        elif project_list:
            query = query.where(Project.name.in_(project_list))
    if source:
        query = query.where(Issue.source == source)
    if issue_type:
        query = query.where(Issue.issue_type == issue_type)
    if action:
        action_list = [a.strip() for a in action.split(",") if a.strip()]
        if len(action_list) == 1:
            query = query.where(LLMEvaluation.suggested_action == action_list[0])
        elif action_list:
            query = query.where(LLMEvaluation.suggested_action.in_(action_list))
    query = _apply_author_role_filter(query, author_role)

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Issue.title.ilike(search_pattern),
                Issue.external_id == search,
            )
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query) or 0
    total_pages = max(1, (total + items_per_page - 1) // items_per_page)

    sort_field = sort_by.lstrip("-")
    sort_desc = sort_by.startswith("-")

    if sort_field not in _VALID_SORT_FIELDS:
        sort_field = "staleness"
        sort_desc = False

    if sort_field == "age":
        col = Issue.created_at
        query = query.order_by(col.asc() if not sort_desc else col.desc())
    elif sort_field == "updated":
        col = Issue.updated_at
        query = query.order_by(col.desc() if not sort_desc else col.asc())
    elif sort_field == "title":
        col = Issue.title
        query = query.order_by(col.asc() if not sort_desc else col.desc())
    elif sort_field == "author":
        col = Issue.author
        query = query.order_by(col.asc() if not sort_desc else col.desc())
    elif sort_field == "number":
        if sort_desc:
            query = query.order_by(Project.name.desc(), Issue.external_id.desc())
        else:
            query = query.order_by(Project.name.asc(), Issue.external_id.asc())
    else:  # staleness (default)
        query = query.order_by(
            func.coalesce(LLMEvaluation.scores["staleness"].as_float(), 0).desc()
        )

    offset = (page - 1) * items_per_page
    query = query.offset(offset).limit(items_per_page)

    result = await session.execute(query)

    issues = []
    for row in result:
        issue = row[0]
        scores = row.scores or {}
        issues.append(
            {
                "project_name": row.project_name,
                "source": issue.source,
                "external_id": issue.external_id,
                "issue_type": issue.issue_type,
                "title": issue.title,
                "author": issue.author,
                "url": issue.url,
                "age_days": _compute_age_days(issue.created_at),
                "staleness": scores.get("staleness"),
                "suggested_action": row.suggested_action,
                "summary": row.summary,
            }
        )

    return issues, total_pages


@router.get("", response_class=HTMLResponse)
async def issue_list(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    source: str = Query("", alias="source"),
    issue_type: str = Query("", alias="type"),
    action: str = Query("", alias="action"),
    author_role: str = Query("", alias="author_role"),
    sort: str = Query("staleness", alias="sort"),
    page: int = Query(1, ge=1),
    search: str = Query("", alias="search"),
    per_page: int = Query(DEFAULT_PER_PAGE, alias="per_page"),
) -> HTMLResponse:
    """Render the issue triage list page."""
    templates: Jinja2Templates = request.app.state.templates
    effective_per_page = per_page if per_page in VALID_PER_PAGE else DEFAULT_PER_PAGE

    issues, total_pages = await _query_issues(
        session,
        project=project,
        source=source,
        issue_type=issue_type,
        action=action,
        author_role=author_role,
        sort_by=sort,
        page=page,
        search=search,
        items_per_page=effective_per_page,
    )

    project_result = await session.execute(
        select(Project.name).order_by(Project.display_order)
    )
    project_names = [row.name for row in project_result]

    return templates.TemplateResponse(
        request,
        "issues/list.html",
        {
            "issues": issues,
            "project_names": project_names,
            "filter_project": project,
            "filter_source": source,
            "filter_type": issue_type,
            "filter_action": action,
            "filter_author_role": author_role,
            "filter_search": search,
            "sort_by": sort,
            "page": page,
            "total_pages": total_pages,
            "per_page": effective_per_page,
        },
    )


@router.get("/table", response_class=HTMLResponse)
async def issue_table_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    project: str = Query("", alias="project"),
    source: str = Query("", alias="source"),
    issue_type: str = Query("", alias="type"),
    action: str = Query("", alias="action"),
    author_role: str = Query("", alias="author_role"),
    sort: str = Query("staleness", alias="sort"),
    page: int = Query(1, ge=1),
    search: str = Query("", alias="search"),
    per_page: int = Query(DEFAULT_PER_PAGE, alias="per_page"),
) -> HTMLResponse:
    """Return just the issue table partial (for HTMX swapping)."""
    templates: Jinja2Templates = request.app.state.templates
    effective_per_page = per_page if per_page in VALID_PER_PAGE else DEFAULT_PER_PAGE

    issues, total_pages = await _query_issues(
        session,
        project=project,
        source=source,
        issue_type=issue_type,
        action=action,
        author_role=author_role,
        sort_by=sort,
        page=page,
        search=search,
        items_per_page=effective_per_page,
    )

    return templates.TemplateResponse(
        request,
        "issues/partials/issue_table.html",
        {
            "issues": issues,
            "filter_project": project,
            "filter_source": source,
            "filter_type": issue_type,
            "filter_action": action,
            "filter_author_role": author_role,
            "filter_search": search,
            "sort_by": sort,
            "page": page,
            "total_pages": total_pages,
            "per_page": effective_per_page,
        },
    )
