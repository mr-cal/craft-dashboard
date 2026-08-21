"""Issue and PR triage routes."""

import ipaddress
import logging
from dataclasses import asdict
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TypedDict, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.llm.client import OPENROUTER_BASE_URL
from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION, _compute_content_hash
from craft_dashboard.models.views import IssueFilters, IssueView
from craft_dashboard.repositories.issue_repository import IssueRepository
from craft_dashboard.routes.eval_api import limiter

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/issues")

VALID_PER_PAGE = {100, 250, 1000}
PER_PAGE_ALL = 0  # sentinel for "show all"
DEFAULT_PER_PAGE = 100

# Semantic search only ever fires on explicit submit (Enter/search button,
# not live-as-you-type), but it still calls an external OpenRouter embedding
# API per search, so it's rate-limited separately from the rest of the
# (free, local) issue list/table endpoints. Mirrors the local/external split
# used for the eval worker's endpoints in eval_api.py.
SEMANTIC_SEARCH_QUERY_MAX_LENGTH = 200


def _is_local_caller(key: str) -> bool:
    """Return whether *key* (the caller's IP) counts as "local" for rate limits."""
    try:
        return ipaddress.ip_address(key).is_private
    except ValueError:
        return False


def _semantic_search_rate_limit(key: str) -> str:
    """Return a higher semantic-search rate limit for local callers."""
    return "1000/minute" if _is_local_caller(key) else "20/minute"


def _semantic_search_cost(request: Request) -> int:
    """Return the rate-limit "cost" of a request to a shared issues endpoint.

    slowapi's ``exempt_when`` callable takes no arguments, so it can't see
    the request's query params. Using ``cost`` instead (which *does* receive
    the request) lets us only count against the limit when the request
    actually carries a non-empty ``search`` query param (i.e. will trigger a
    semantic-search embedding call); plain filter/sort/pagination requests
    to the same endpoint cost 0 and never count against the limit.
    """
    return 1 if request.query_params.get("search", "").strip() else 0


ALL_SCORES = {
    "staleness": "Staleness",
    "complexity": "Complexity",
    "support_request": "Support Request",
    "confidence": "Confidence",
}
# Scores where a higher value is better (green) rather than worse (red)
INVERTED_SCORES: frozenset[str] = frozenset()
DEFAULT_SCORES = "staleness,confidence"


class IssueTemplateContext(TypedDict):
    """Template context used by issue list and partial responses."""

    issues: list[IssueView]
    semantic_issues: list[IssueView]
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


async def _run_semantic_search(
    session: AsyncSession,
    *,
    filters: IssueFilters,
    existing_issue_ids: set[int],
    openrouter_api_key: str,
    embedding_model: str,
    top_n: int,
    similarity_threshold: float,
    filtered_issues: dict[str, list[str]] | None = None,
) -> list[IssueView]:
    """Return semantically-similar issues not already in existing_issue_ids.

    Truncated/invalid queries are handled by the caller (query length is
    capped before this is called). Any embedding/query failure is logged and
    treated as "no semantic results" rather than failing the whole page, so
    a flaky OpenRouter call never breaks basic issue-list browsing.
    """
    query = filters.search.strip()
    if not query or not openrouter_api_key:
        return []

    embed_client = EmbeddingClient(
        base_url=OPENROUTER_BASE_URL,
        model=embedding_model,
        api_key=openrouter_api_key,
        ca_cert="",
    )
    try:
        query_embedding = await embed_client.embed(query, dimensions=1024)
    except Exception:  # noqa: BLE001
        logger.warning("Semantic search embedding failed for query", exc_info=True)
        return []
    finally:
        await embed_client.close()

    repo = IssueRepository(session, filtered_issues=filtered_issues)
    try:
        return await repo.semantic_search(
            query_embedding=query_embedding,
            exclude_issue_ids=existing_issue_ids,
            limit=top_n,
            similarity_threshold=similarity_threshold,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Semantic search query failed", exc_info=True)
        return []


async def _build_issue_context(
    session: AsyncSession,
    *,
    filters: IssueFilters,
    scores: str,
    filtered_issues: dict[str, list[str]] | None = None,
    openrouter_api_key: str = "",
    semantic_search_embedding_model: str = "",
    semantic_search_top_n: int = 10,
    semantic_search_similarity_threshold: float = 0.70,
) -> IssueTemplateContext:
    """Build the template context for issue list rendering.

    When filters.search is set, literal (ILIKE) matches are returned first,
    exactly as ranked by IssueRepository.search(); any additional issues
    found only via semantic search are appended below as a second tier (no
    score blending between the two, since ILIKE match and cosine distance
    aren't on comparable scales).
    """
    repo = IssueRepository(session, filtered_issues=filtered_issues)
    result = await repo.search(filters)
    project_names = await repo.get_project_names()

    semantic_issues: list[IssueView] = []
    if filters.search.strip():
        semantic_issues = await _run_semantic_search(
            session,
            filters=filters,
            existing_issue_ids={issue.id for issue in result.issues},
            openrouter_api_key=openrouter_api_key,
            embedding_model=semantic_search_embedding_model,
            top_n=semantic_search_top_n,
            similarity_threshold=semantic_search_similarity_threshold,
            filtered_issues=filtered_issues,
        )

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
        "semantic_issues": semantic_issues,
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
    complexity = "complexity"
    support_request = "support_request"
    confidence = "confidence"
    age = "age"
    updated = "updated"
    title = "title"
    author = "author"
    number = "number"


def _build_original_issue_url(issue: dict[str, Any]) -> str:
    """Build the upstream issue URL from issue data."""
    if issue["source"] == "launchpad":
        lp_name = issue["project_name"].removesuffix(" (launchpad)")
        return f"https://bugs.launchpad.net/{lp_name}/+bug/{issue['external_id']}"
    return (
        f"https://github.com/canonical/{issue['project_name']}/issues/"
        f"{issue['external_id']}"
    )


@router.get("", response_class=HTMLResponse)
@limiter.limit(_semantic_search_rate_limit, cost=_semantic_search_cost)
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
    search: str = Query(
        "", alias="search", max_length=SEMANTIC_SEARCH_QUERY_MAX_LENGTH
    ),
    per_page: str = Query("", alias="per_page"),
    scores: str = Query(DEFAULT_SCORES, alias="scores"),
    llm_status: str = Query("", alias="llm_status"),
) -> HTMLResponse:
    """Render the issue triage list page."""
    templates: Jinja2Templates = request.app.state.templates
    settings = request.app.state.settings
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
    context = await _build_issue_context(
        session,
        filters=filters,
        scores=scores,
        filtered_issues=get_config(request).filtered_issues,
        openrouter_api_key=settings.openrouter_api_key,
        semantic_search_embedding_model=settings.semantic_search_embedding_model,
        semantic_search_top_n=settings.semantic_search_top_n,
        semantic_search_similarity_threshold=settings.semantic_search_similarity_threshold,
    )

    return templates.TemplateResponse(
        request,
        "issues/list.html",
        cast(dict[str, Any], dict(context)),
    )


@router.get("/table", response_class=HTMLResponse)
@limiter.limit(_semantic_search_rate_limit, cost=_semantic_search_cost)
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
    search: str = Query(
        "", alias="search", max_length=SEMANTIC_SEARCH_QUERY_MAX_LENGTH
    ),
    per_page: str = Query("", alias="per_page"),
    scores: str = Query(DEFAULT_SCORES, alias="scores"),
    llm_status: str = Query("", alias="llm_status"),
) -> HTMLResponse:
    """Return just the issue table partial (for HTMX swapping)."""
    templates: Jinja2Templates = request.app.state.templates
    settings = request.app.state.settings
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
    context = await _build_issue_context(
        session,
        filters=filters,
        scores=scores,
        filtered_issues=get_config(request).filtered_issues,
        openrouter_api_key=settings.openrouter_api_key,
        semantic_search_embedding_model=settings.semantic_search_embedding_model,
        semantic_search_top_n=settings.semantic_search_top_n,
        semantic_search_similarity_threshold=settings.semantic_search_similarity_threshold,
    )

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
    """Render the issue detail page with evaluation history and related issues."""
    templates: Jinja2Templates = request.app.state.templates
    settings = request.app.state.settings
    repo = IssueRepository(session, filtered_issues=get_config(request).filtered_issues)
    issue = await repo.get_issue_detail(project, number)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    evaluation_history = cast(list[dict[str, Any]], issue["evaluation_history"])
    current_evaluation = evaluation_history[0] if evaluation_history else None

    activity_history = await repo.get_issue_activity_history(project, number)

    has_embedding = bool(current_evaluation and current_evaluation.get("has_embedding"))

    is_outdated = False
    if current_evaluation:
        if current_evaluation.get("eval_version") != CURRENT_EVAL_VERSION:
            is_outdated = True
        else:
            stored_hash = current_evaluation.get("issue_data_hash")
            if stored_hash:
                current_hash = _compute_content_hash(
                    issue["title"],
                    issue.get("body"),
                    issue["state"],
                    issue.get("labels") or [],
                    issue.get("comments") or [],
                )
                if stored_hash != current_hash:
                    is_outdated = True

    related_issues: list[dict[str, Any]] = []
    try:
        related_issues = await repo.find_similar_issues(
            issue_id=issue["id"],
            top_n=settings.related_issues_top_n,
            similarity_threshold=settings.related_issues_similarity_threshold,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "find_similar_issues failed for issue %s", issue["id"], exc_info=True
        )

    return templates.TemplateResponse(
        request,
        "issues/detail.html",
        {
            "issue": issue,
            "current_evaluation": current_evaluation,
            "evaluation_history": evaluation_history,
            "activity_history": activity_history,
            "original_issue_url": _build_original_issue_url(issue),
            "related_issues": related_issues,
            "has_embedding": has_embedding,
            "is_outdated": is_outdated,
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
    del scores, per_page

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
    repo = IssueRepository(session, filtered_issues=get_config(request).filtered_issues)
    result = await repo.search(filters)
    payload = jsonable_encoder([asdict(issue) for issue in result.issues])

    return JSONResponse(
        payload,
        headers={"Content-Disposition": 'attachment; filename="issues-export.json"'},
    )
