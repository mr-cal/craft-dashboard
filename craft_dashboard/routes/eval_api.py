"""Eval API routes for pull-based issue evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel
from scripts.llm.validation import validate_evaluation_result
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import aliased

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.auth import verify_eval_token
from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION, _compute_content_hash
from craft_dashboard.llm.exceptions import LLMValidationError
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.repositories.issue_repository import (
    _build_excluded_issues_condition,
)

router = APIRouter(prefix="/api/eval")
limiter = Limiter(key_func=get_remote_address)

_LOCK_TTL = timedelta(minutes=10)


class EvalResultSubmission(BaseModel):
    """Request body for a submitted evaluation result."""

    issue_id: int
    content_hash: str
    summary: str
    scores: dict[str, int | float]
    suggested_action: str | None = None
    suggested_action_reason: str | None = None
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_used: str = ""
    llm_backend: str = "local"
    summary_embedding: list[float] | None = None


class EmbedResultSubmission(BaseModel):
    """Request body for a submitted embedding result."""

    issue_id: int
    summary_embedding: list[float]


def _require_eval_auth(request: Request, authorization: str = "") -> None:
    """Require a valid eval API bearer token."""
    eval_api_token = request.app.state.settings.eval_api_token
    verify_eval_token(authorization, eval_api_token)


def _current_content_hash(issue: Issue) -> str:
    """Compute the current content hash for an issue."""
    return _compute_content_hash(
        issue.title,
        issue.body,
        issue.state,
        issue.labels or [],
        issue.comments or [],
    )


async def _fetch_issue_and_latest_evaluation(
    session: AsyncSession,
    *,
    project: str = "",
    open_only: bool = True,
    force: bool = False,
    incomplete: bool = False,
    stale_days: int = 0,
    filtered_issues: dict[str, list[str]] | None = None,
) -> tuple[Issue, str, LLMEvaluation | None] | None:
    latest_evaluation = aliased(LLMEvaluation)
    now = datetime.now(tz=UTC)
    old_version = or_(
        latest_evaluation.eval_version.is_(None),
        latest_evaluation.eval_version != CURRENT_EVAL_VERSION,
    )
    priority = case(
        (latest_evaluation.id.is_(None) & (Issue.state == "open"), 1),
        (latest_evaluation.id.is_(None) & (Issue.state != "open"), 2),
        (old_version & (Issue.state == "open"), 3),
        (old_version & (Issue.state != "open"), 4),
        else_=5,
    )
    open_first = case((Issue.state == "open", 0), else_=1)
    query = (
        select(Issue, Project.name, latest_evaluation)
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            latest_evaluation,
            (latest_evaluation.issue_id == Issue.id)
            & latest_evaluation.latest.is_(True),
        )
        .where(
            or_(
                latest_evaluation.eval_locked_until.is_(None),
                latest_evaluation.eval_locked_until <= now,
            )
        )
        .order_by(priority, open_first, Issue.id)
    )

    excl = _build_excluded_issues_condition(filtered_issues or {})
    if excl is not None:
        query = query.where(excl)

    if open_only:
        query = query.where(Issue.state == "open")

    if project:
        query = query.where(Project.name == project)

    if incomplete:
        query = query.where(
            or_(
                latest_evaluation.id.is_(None),
                latest_evaluation.summary.is_(None),
                latest_evaluation.summary == "",
                (
                    (Issue.state == "open")
                    & or_(
                        latest_evaluation.scores.is_(None),
                        latest_evaluation.suggested_action.is_(None),
                        latest_evaluation.suggested_action == "",
                    )
                ),
            )
        )

    if stale_days > 0:
        cutoff = now - timedelta(days=stale_days)
        query = query.where(
            or_(
                latest_evaluation.id.is_(None),
                latest_evaluation.evaluated_at < cutoff,
            )
        )

    result = await session.execute(query)
    for row in result.all():
        issue, project_name, evaluation = row[0], row[1], row[2]
        current_hash = _current_content_hash(issue)
        has_complete_evaluation = (
            evaluation is not None
            and evaluation.summary not in (None, "")
            and (
                issue.state != "open"
                or (
                    evaluation.scores is not None
                    and evaluation.suggested_action not in (None, "")
                )
            )
        )
        if (
            not force
            and stale_days <= 0
            and not incomplete
            and has_complete_evaluation
            and evaluation.eval_version == CURRENT_EVAL_VERSION
            and evaluation.issue_data_hash == current_hash
        ):
            continue
        return issue, project_name, evaluation
    return None


@router.get("/next", response_model=None)
@limiter.limit("30/minute")
async def next_issue(
    request: Request,
    *,
    authorization: str = Header(default=""),
    project: str = Query(default=""),
    open_only: bool = Query(default=True),
    force: bool = Query(default=False),
    incomplete: bool = Query(default=False),
    stale_days: int = Query(default=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any] | Response:
    """Return the next issue that needs evaluation, if any."""
    _require_eval_auth(request, authorization)

    row = await _fetch_issue_and_latest_evaluation(
        session,
        project=project,
        open_only=open_only,
        force=force,
        incomplete=incomplete,
        stale_days=stale_days,
        filtered_issues=get_config(request).filtered_issues,
    )
    if row is None:
        return Response(status_code=204)

    issue, project_name, latest_evaluation = row
    lock_until = datetime.now(tz=UTC) + _LOCK_TTL
    if latest_evaluation is None:
        session.add(
            LLMEvaluation(
                issue_id=issue.id,
                model_name="pending",
                latest=True,
                eval_locked_until=lock_until,
            )
        )
    else:
        latest_evaluation.eval_locked_until = lock_until

    await session.commit()

    return {
        "issue_id": issue.id,
        "project_name": project_name,
        "external_id": issue.external_id,
        "title": issue.title,
        "state": issue.state,
        "issue_type": issue.issue_type,
        "body": issue.body,
        "comments": issue.comments or [],
        "labels": issue.labels or [],
        "author": issue.author,
        "author_association": (
            "MAINTAINER" if issue.author_is_maintainer else "CONTRIBUTOR"
        ),
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
        "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
        "current_hash": _current_content_hash(issue),
        "maintainers": list(get_config(request).maintainers),
    }


@router.post("/result")
async def submit_result(
    request: Request,
    payload: EvalResultSubmission,
    *,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Store an evaluation result for an issue."""
    _require_eval_auth(request, authorization)

    issue = await session.get(Issue, payload.issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    current_hash = _current_content_hash(issue)
    if payload.content_hash != current_hash:
        raise HTTPException(
            status_code=409,
            detail="Content hash mismatch; issue content has changed.",
        )

    result = cast(
        dict[str, object],
        {
            "summary": payload.summary,
            "scores": payload.scores,
            "suggested_action": payload.suggested_action,
            "suggested_action_reason": payload.suggested_action_reason,
        },
    )
    try:
        validate_evaluation_result(
            result, issue_type=issue.issue_type, state=issue.state
        )
    except LLMValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await session.execute(
        update(LLMEvaluation)
        .where(
            LLMEvaluation.issue_id == payload.issue_id,
            LLMEvaluation.latest.is_(True),
        )
        .values(latest=False, eval_locked_until=None)
    )
    session.add(
        LLMEvaluation(
            issue_id=payload.issue_id,
            model_name=payload.model_used,
            eval_version=CURRENT_EVAL_VERSION,
            summary=payload.summary,
            suggested_action=payload.suggested_action,
            suggested_action_reason=payload.suggested_action_reason,
            scores=payload.scores,
            tokens_used=payload.tokens_used,
            prompt_tokens=payload.prompt_tokens,
            completion_tokens=payload.completion_tokens,
            llm_backend=payload.llm_backend,
            evaluated_at=datetime.now(tz=UTC),
            issue_data_hash=current_hash,
            latest=True,
            eval_locked_until=datetime.now(tz=UTC) + _LOCK_TTL,
            summary_embedding=payload.summary_embedding,
        )
    )

    await session.commit()
    return {"status": "stored", "issue_id": payload.issue_id}


@router.get("/status")
async def eval_status(
    request: Request,
    *,
    authorization: str = Header(default=""),
    project: str = Query(default=""),
    open_only: bool = Query(default=True),
    force: bool = Query(default=False),
    incomplete: bool = Query(default=False),
    stale_days: int = Query(default=0),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
    """Return summary counts for the eval queue."""
    _require_eval_auth(request, authorization)

    now = datetime.now(tz=UTC)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    excl = _build_excluded_issues_condition(get_config(request).filtered_issues)

    locked = await session.scalar(
        select(func.count(LLMEvaluation.id)).where(
            LLMEvaluation.eval_locked_until > now
        )
    )
    evaluated_today = await session.scalar(
        select(func.count(LLMEvaluation.id)).where(
            LLMEvaluation.evaluated_at >= today_midnight,
            LLMEvaluation.model_name != "pending",
        )
    )
    total_evaluated = await session.scalar(
        select(func.count(LLMEvaluation.id)).where(
            LLMEvaluation.latest.is_(True),
            LLMEvaluation.model_name != "pending",
        )
    )

    # Build base issue query (no latest_evaluation join — used for total_open)
    base_query = select(Issue.id).join(Project, Issue.project_id == Project.id)
    if open_only:
        base_query = base_query.where(Issue.state == "open")
    if project:
        base_query = base_query.where(Project.name == project)
    if excl is not None:
        base_query = base_query.where(excl)
    total_open = await session.scalar(
        select(func.count()).select_from(base_query.subquery())
    )

    # Pending count depends on filter mode
    if force:
        pending = total_open
    else:
        latest_evaluation = aliased(LLMEvaluation)
        pending_query = (
            select(Issue.id)
            .join(Project, Issue.project_id == Project.id)
            .outerjoin(
                latest_evaluation,
                (latest_evaluation.issue_id == Issue.id)
                & latest_evaluation.latest.is_(True),
            )
        )
        if open_only:
            pending_query = pending_query.where(Issue.state == "open")
        if project:
            pending_query = pending_query.where(Project.name == project)
        if excl is not None:
            pending_query = pending_query.where(excl)

        if incomplete:
            pending_query = pending_query.where(
                or_(
                    latest_evaluation.id.is_(None),
                    latest_evaluation.summary.is_(None),
                    latest_evaluation.summary == "",
                    (
                        (Issue.state == "open")
                        & or_(
                            latest_evaluation.scores.is_(None),
                            latest_evaluation.suggested_action.is_(None),
                            latest_evaluation.suggested_action == "",
                        )
                    ),
                )
            )
        elif stale_days > 0:
            cutoff = now - timedelta(days=stale_days)
            pending_query = pending_query.where(
                or_(
                    latest_evaluation.id.is_(None),
                    latest_evaluation.evaluated_at < cutoff,
                )
            )
        else:
            pending_query = pending_query.where(
                or_(
                    latest_evaluation.id.is_(None),
                    (
                        (latest_evaluation.model_name == "pending")
                        & or_(
                            latest_evaluation.eval_locked_until.is_(None),
                            latest_evaluation.eval_locked_until <= now,
                        )
                    ),
                )
            )

        pending = await session.scalar(
            select(func.count()).select_from(pending_query.subquery())
        )

    pending_embeddings_q = (
        select(func.count(LLMEvaluation.id))
        .join(Issue, LLMEvaluation.issue_id == Issue.id)
        .join(Project, Issue.project_id == Project.id)
        .where(
            LLMEvaluation.latest.is_(True),
            LLMEvaluation.model_name != "pending",
            LLMEvaluation.summary.isnot(None),
            LLMEvaluation.summary != "",
            LLMEvaluation.summary_embedding.is_(None),
        )
    )
    if excl is not None:
        pending_embeddings_q = pending_embeddings_q.where(excl)
    pending_embeddings = await session.scalar(pending_embeddings_q)

    return {
        "pending": pending or 0,
        "locked": locked or 0,
        "evaluated_today": evaluated_today or 0,
        "total_evaluated": total_evaluated or 0,
        "total_open": total_open or 0,
        "pending_embeddings": pending_embeddings or 0,
    }


_EMBED_LOCK_TTL = timedelta(minutes=5)


@router.get("/embed-next", response_model=None)
@limiter.limit("300/minute")
async def embed_next(
    request: Request,
    *,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any] | Response:
    """Return the next evaluated issue that needs an embedding, if any."""
    _require_eval_auth(request, authorization)

    excl = _build_excluded_issues_condition(get_config(request).filtered_issues)
    now = datetime.now(tz=UTC)
    embed_q = (
        select(LLMEvaluation, Issue.title, Issue.external_id, Project.name)
        .join(Issue, LLMEvaluation.issue_id == Issue.id)
        .join(Project, Issue.project_id == Project.id)
        .where(
            LLMEvaluation.latest.is_(True),
            LLMEvaluation.model_name != "pending",
            LLMEvaluation.summary.isnot(None),
            LLMEvaluation.summary != "",
            LLMEvaluation.summary_embedding.is_(None),
            or_(
                LLMEvaluation.eval_locked_until.is_(None),
                LLMEvaluation.eval_locked_until <= now,
            ),
        )
        .order_by(Issue.id)
        .limit(1)
    )
    if excl is not None:
        embed_q = embed_q.where(excl)
    result = await session.execute(embed_q)
    row = result.first()
    if row is None:
        return Response(status_code=204)

    evaluation, title, external_id, project_name = row
    evaluation.eval_locked_until = now + _EMBED_LOCK_TTL
    await session.commit()

    embed_text = f"{title}. {evaluation.summary}"
    return {
        "issue_id": evaluation.issue_id,
        "project_name": project_name,
        "external_id": external_id,
        "embed_text": embed_text,
    }


@router.post("/embed-result")
async def submit_embed_result(
    request: Request,
    payload: EmbedResultSubmission,
    *,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Store an embedding for a previously evaluated issue."""
    _require_eval_auth(request, authorization)

    if not payload.summary_embedding:
        raise HTTPException(
            status_code=422, detail="summary_embedding must not be empty"
        )

    result = await session.execute(
        select(LLMEvaluation).where(
            LLMEvaluation.issue_id == payload.issue_id,
            LLMEvaluation.latest.is_(True),
        )
    )
    evaluation = result.scalar_one_or_none()
    if evaluation is None:
        raise HTTPException(
            status_code=404, detail="No evaluation found for this issue"
        )

    evaluation.summary_embedding = payload.summary_embedding
    evaluation.eval_locked_until = None
    await session.commit()
    return {"status": "stored", "issue_id": payload.issue_id}
