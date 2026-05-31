"""Eval API routes for pull-based issue evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from scripts.llm.validation import validate_evaluation_result
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import aliased

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.auth import verify_eval_token
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.llm.evaluator import _compute_content_hash
from craft_dashboard.llm.exceptions import LLMValidationError
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project

router = APIRouter(prefix="/api/eval")
limiter = Limiter(key_func=get_remote_address)

_LOCK_TTL = timedelta(minutes=10)
_DUPLICATE_LOCK_TTL = timedelta(minutes=30)
_EMBEDDING_DIMENSION = 768


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
    embedding: list[float] | None = None


class SimilarRequest(BaseModel):
    """Request body for finding similar evaluations."""

    embedding: list[float]
    exclude_issue_id: int
    limit: int = Field(default=5, ge=1, le=50)
    cosine_threshold: float = Field(default=0.15, ge=0.0, le=2.0)

    def model_post_init(self, /, __context: object) -> None:
        """Validate that the embedding dimension matches the expected size."""
        if len(self.embedding) != _EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding must have {_EMBEDDING_DIMENSION} dimensions, "
                f"got {len(self.embedding)}"
            )


class DuplicateResultSubmission(BaseModel):
    """Request body for submitting phase-2 duplicate detection results."""

    evaluation_id: int
    duplicateness: float = Field(ge=0.0, le=100.0)
    candidates_compared: int = Field(ge=0)
    duplicate_of_issue_id: int | None = None
    updated_summary: str | None = None
    updated_embedding: list[float] | None = None


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
) -> tuple[Issue, str, LLMEvaluation | None] | None:
    latest_evaluation = aliased(LLMEvaluation)
    now = datetime.now(tz=UTC)
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
        .order_by(Issue.id)
    )

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
        "maintainers": list(request.app.state.config.maintainers),
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
            eval_locked_until=None,
            summary_embedding=payload.embedding,
        )
    )

    await session.commit()
    return {"status": "stored", "issue_id": payload.issue_id}


@router.get("/status")
async def eval_status(
    request: Request,
    *,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
    """Return summary counts for the eval queue."""
    _require_eval_auth(request, authorization)

    now = datetime.now(tz=UTC)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    latest_evaluation = aliased(LLMEvaluation)
    pending = await session.scalar(
        select(func.count(Issue.id))
        .outerjoin(
            latest_evaluation,
            (latest_evaluation.issue_id == Issue.id)
            & latest_evaluation.latest.is_(True),
        )
        .where(
            Issue.state == "open",
            or_(
                latest_evaluation.id.is_(None),
                (
                    (latest_evaluation.model_name == "pending")
                    & or_(
                        latest_evaluation.eval_locked_until.is_(None),
                        latest_evaluation.eval_locked_until <= now,
                    )
                ),
            ),
        )
    )
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

    return {
        "pending": pending or 0,
        "locked": locked or 0,
        "evaluated_today": evaluated_today or 0,
        "total_evaluated": total_evaluated or 0,
    }


@router.get("/duplicate-work", response_model=None)
async def duplicate_work(
    request: Request,
    *,
    authorization: str = Header(default=""),
    limit: int = Query(default=1, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any] | Response:
    """Return the next evaluation that needs phase-2 duplicate detection.

    Eligible: latest=True, summary_embedding IS NOT NULL, no 'duplicateness'
    key in scores, not currently locked for duplicate processing.

    Locks the returned evaluation for _DUPLICATE_LOCK_TTL to prevent
    concurrent workers from picking up the same item.
    """
    _require_eval_auth(request, authorization)

    now = datetime.now(tz=UTC)

    result = await session.execute(
        select(LLMEvaluation, Issue, Project)
        .join(Issue, LLMEvaluation.issue_id == Issue.id)
        .join(Project, Issue.project_id == Project.id)
        .where(
            LLMEvaluation.latest.is_(True),
            LLMEvaluation.summary_embedding.is_not(None),
            LLMEvaluation.scores["duplicateness"].as_string().is_(None),
            or_(
                LLMEvaluation.duplicate_locked_until.is_(None),
                LLMEvaluation.duplicate_locked_until <= now,
            ),
        )
        .order_by(LLMEvaluation.id)
        .limit(limit)
    )
    rows = result.all()
    if not rows:
        return Response(status_code=204)

    items = []
    lock_until = now + _DUPLICATE_LOCK_TTL
    for evaluation, issue, project in rows:
        evaluation.duplicate_locked_until = lock_until
        items.append(
            {
                "evaluation_id": evaluation.id,
                "issue_id": issue.id,
                "external_id": issue.external_id,
                "project_name": project.name,
                "title": issue.title,
                "summary": evaluation.summary,
                "embedding": (
                    list(evaluation.summary_embedding)
                    if evaluation.summary_embedding is not None
                    else None
                ),
            }
        )

    await session.commit()
    return {"items": items}


@router.post("/similar")
async def find_similar(
    request: Request,
    payload: SimilarRequest,
    *,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Find evaluations with similar embeddings (cross-project, pgvector).

    Uses cosine distance (<=> operator). Returns candidates ordered by
    distance ascending (most similar first).

    Note: this endpoint requires PostgreSQL with the pgvector extension.
    """
    _require_eval_auth(request, authorization)

    sql = text("""
        SELECT e.id AS evaluation_id,
               e.issue_id,
               i.external_id,
               p.name AS project_name,
               i.title,
               e.summary,
               e.summary_embedding <=> CAST(:embedding AS vector) AS distance
        FROM llm_evaluations e
        JOIN issues i ON e.issue_id = i.id
        JOIN projects p ON i.project_id = p.id
        WHERE e.latest = true
          AND e.summary_embedding IS NOT NULL
          AND i.id != :exclude_issue_id
          AND (e.summary_embedding <=> CAST(:embedding AS vector)) < :threshold
        ORDER BY distance
        LIMIT :limit
    """)
    rows = await session.execute(
        sql,
        {
            "embedding": str(payload.embedding),
            "exclude_issue_id": payload.exclude_issue_id,
            "threshold": payload.cosine_threshold,
            "limit": payload.limit,
        },
    )
    candidates = [dict(row._mapping) for row in rows.fetchall()]  # noqa: SLF001
    return {"candidates": candidates}


@router.post("/duplicate-result")
async def submit_duplicate_result(
    request: Request,
    payload: DuplicateResultSubmission,
    *,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Store the phase-2 duplicate detection result for an evaluation.

    Updates the specific evaluation row identified by evaluation_id. Rejects
    if that row is no longer the latest (i.e., phase 1 re-ran in the meantime).
    """
    _require_eval_auth(request, authorization)

    evaluation = await session.get(LLMEvaluation, payload.evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    if not evaluation.latest:
        raise HTTPException(
            status_code=409,
            detail="Evaluation is no longer the latest; phase 1 may have re-run.",
        )

    scores = dict(evaluation.scores or {})
    scores["duplicateness"] = payload.duplicateness

    values: dict[str, Any] = {
        "scores": scores,
        "candidates_compared": payload.candidates_compared,
        "duplicate_locked_until": None,
    }
    if payload.duplicate_of_issue_id is not None:
        values["duplicate_of_issue_id"] = payload.duplicate_of_issue_id
    if payload.updated_summary is not None:
        values["summary"] = payload.updated_summary
    if payload.updated_embedding is not None:
        values["summary_embedding"] = payload.updated_embedding

    await session.execute(
        update(LLMEvaluation)
        .where(LLMEvaluation.id == payload.evaluation_id)
        .values(**values)
    )
    await session.commit()
    return {"status": "stored", "evaluation_id": payload.evaluation_id}
