"""Issue and PR triage routes."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Integer as SAInteger
from sqlalchemy import cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.views import IssueFilters, IssueQueryResult, IssueView

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates
    from sqlalchemy.sql.elements import ColumnElement

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
DEFAULT_SCORES = "staleness,readiness"


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


_VALID_SORT_FIELDS = {e.value for e in IssueSort}


def _compute_age_days(created_at: datetime | None) -> int | None:
    """Compute days since creation, or None if unknown."""
    if created_at is None:
        return None
    now = datetime.now(tz=UTC)
    created = (
        created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at
    )
    return (now - created).days


def _apply_author_role_filter(query: Select, author_role: str) -> Select:
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
    session: AsyncSession, filters: IssueFilters
) -> IssueQueryResult:
    """Query issues with filters and return typed results."""
    query = (
        select(
            Issue,
            Project.name.label("project_name"),
            LLMEvaluation.summary,
            LLMEvaluation.suggested_action,
            LLMEvaluation.suggested_action_reason,
            LLMEvaluation.scores,
        )
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest.is_(True),
        )
    )

    if filters.state:
        state_list = [s.strip() for s in filters.state.split(",") if s.strip()]
        if len(state_list) == 1:
            query = query.where(Issue.state == state_list[0])
        elif state_list:
            query = query.where(Issue.state.in_(state_list))

    if filters.project:
        project_list = [p.strip() for p in filters.project.split(",") if p.strip()]
        if len(project_list) == 1:
            query = query.where(Project.name == project_list[0])
        elif project_list:
            query = query.where(Project.name.in_(project_list))
    if filters.source:
        query = query.where(Issue.source == filters.source)
    if filters.issue_type:
        type_list = [t.strip() for t in filters.issue_type.split(",") if t.strip()]
        if len(type_list) == 1:
            query = query.where(Issue.issue_type == type_list[0])
        elif type_list:
            query = query.where(Issue.issue_type.in_(type_list))
    if filters.action:
        action_list = [a.strip() for a in filters.action.split(",") if a.strip()]
        if len(action_list) == 1:
            query = query.where(LLMEvaluation.suggested_action == action_list[0])
        elif action_list:
            query = query.where(LLMEvaluation.suggested_action.in_(action_list))
    query = _apply_author_role_filter(query, filters.author_role)

    if filters.search:
        tokens = filters.search.strip().split()
        for token in tokens:
            clean = token.lstrip("#")
            conditions: list[ColumnElement[bool]] = [
                Issue.title.ilike(f"%{token}%"),
                Issue.author.ilike(f"%{token}%"),
                Project.name.ilike(f"%{token}%"),
                LLMEvaluation.summary.ilike(f"%{token}%"),
            ]
            if clean.isdigit():
                conditions.append(Issue.external_id == clean)
            query = query.where(or_(*conditions))

    if filters.llm_status == "no_llm":
        query = query.where(LLMEvaluation.id.is_(None))
    elif filters.llm_status == "partial_llm":
        query = query.where(
            LLMEvaluation.id.is_not(None)
            & (
                LLMEvaluation.summary.is_(None)
                | (LLMEvaluation.summary == "")
                | LLMEvaluation.suggested_action.is_(None)
                | (LLMEvaluation.suggested_action == "")
                | LLMEvaluation.scores.is_(None)
            )
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = await session.scalar(count_query) or 0
    if filters.items_per_page <= 0:
        total_pages = 1
        page = 1
    else:
        total_pages = max(
            1, (total + filters.items_per_page - 1) // filters.items_per_page
        )
        page = min(filters.page, total_pages)

    sort_field = filters.sort_by.lstrip("-")
    sort_desc = filters.sort_by.startswith("-")

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
        numeric_id = cast(Issue.external_id, SAInteger)
        if sort_desc:
            query = query.order_by(Project.name.desc(), numeric_id.desc())
        else:
            query = query.order_by(Project.name.asc(), numeric_id.asc())
    elif sort_field in ALL_SCORES:
        query = query.order_by(
            func.coalesce(LLMEvaluation.scores[sort_field].as_float(), 0).desc()
            if not sort_desc
            else func.coalesce(LLMEvaluation.scores[sort_field].as_float(), 0).asc()
        )

    if filters.items_per_page > 0:
        offset = (page - 1) * filters.items_per_page
        query = query.offset(offset).limit(filters.items_per_page)

    result = await session.execute(query)

    issues = []
    for row in result:
        issue = row[0]
        scores = row.scores or {}
        issues.append(
            IssueView(
                project_name=row.project_name,
                source=issue.source,
                external_id=issue.external_id,
                issue_type=issue.issue_type,
                title=issue.title,
                author=issue.author,
                url=issue.url,
                age_days=_compute_age_days(issue.created_at),
                staleness=scores.get("staleness"),
                duplicateness=scores.get("duplicateness"),
                complexity=scores.get("complexity"),
                support_request=scores.get("support_request"),
                readiness=scores.get("readiness"),
                suggested_action=row.suggested_action,
                suggested_action_reason=row.suggested_action_reason,
                summary=row.summary,
            )
        )

    return IssueQueryResult(
        issues=issues,
        total_count=total,
        total_pages=total_pages,
        page=page,
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
    per_page: int = Query(DEFAULT_PER_PAGE, alias="per_page"),
    scores: str = Query(DEFAULT_SCORES, alias="scores"),
    llm_status: str = Query("", alias="llm_status"),
) -> HTMLResponse:
    """Render the issue triage list page."""
    templates: Jinja2Templates = request.app.state.templates
    effective_per_page = (
        per_page
        if per_page in VALID_PER_PAGE or per_page == PER_PAGE_ALL
        else DEFAULT_PER_PAGE
    )

    active_scores = [s.strip() for s in scores.split(",") if s.strip() in ALL_SCORES]
    if not active_scores:
        active_scores = DEFAULT_SCORES.split(",")

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
    result = await _query_issues(session, filters)

    project_result = await session.execute(
        select(Project.name)
        .where(Project.category != "aggregate")
        .order_by(Project.display_order)
    )
    project_names = [row.name for row in project_result]

    return templates.TemplateResponse(
        request,
        "issues/list.html",
        {
            "issues": result.issues,
            "project_names": project_names,
            "filter_project": project,
            "filter_source": source,
            "filter_state": state,
            "filter_type": issue_type,
            "filter_action": action,
            "filter_author_role": author_role,
            "filter_search": search,
            "sort_by": sort,
            "page": result.page,
            "total_pages": result.total_pages,
            "per_page": effective_per_page,
            "filter_scores": scores,
            "active_scores": active_scores,
            "all_scores": ALL_SCORES,
            "filter_llm_status": llm_status,
            "total_count": result.total_count,
        },
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
    per_page: int = Query(DEFAULT_PER_PAGE, alias="per_page"),
    scores: str = Query(DEFAULT_SCORES, alias="scores"),
    llm_status: str = Query("", alias="llm_status"),
) -> HTMLResponse:
    """Return just the issue table partial (for HTMX swapping)."""
    templates: Jinja2Templates = request.app.state.templates
    effective_per_page = (
        per_page
        if per_page in VALID_PER_PAGE or per_page == PER_PAGE_ALL
        else DEFAULT_PER_PAGE
    )

    active_scores = [s.strip() for s in scores.split(",") if s.strip() in ALL_SCORES]
    if not active_scores:
        active_scores = DEFAULT_SCORES.split(",")

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
    result = await _query_issues(session, filters)

    return templates.TemplateResponse(
        request,
        "issues/partials/issue_table.html",
        {
            "issues": result.issues,
            "filter_project": project,
            "filter_source": source,
            "filter_state": state,
            "filter_type": issue_type,
            "filter_action": action,
            "filter_author_role": author_role,
            "filter_search": search,
            "sort_by": sort,
            "page": result.page,
            "total_pages": result.total_pages,
            "per_page": effective_per_page,
            "filter_scores": scores,
            "active_scores": active_scores,
            "all_scores": ALL_SCORES,
            "filter_llm_status": llm_status,
            "total_count": result.total_count,
        },
    )
