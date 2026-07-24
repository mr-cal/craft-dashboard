"""Eval API routes for pull-based issue evaluation."""

from __future__ import annotations

import ipaddress
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel
from scripts.llm.validation import validate_evaluation_result
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.auth import verify_eval_token
from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.llm.evaluation_queue import build_pending_evaluation_query
from craft_dashboard.llm.evaluator import CURRENT_EVAL_VERSION
from craft_dashboard.llm.exceptions import LLMValidationError
from craft_dashboard.models.eval_queue_snapshot import EvalQueueSnapshot
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.repositories.issue_repository import (
    _build_excluded_issues_condition,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/eval")
limiter = Limiter(key_func=get_remote_address)

_LOCK_TTL = timedelta(minutes=10)

#: How recently `/next`/`/result` must have been called for the service to

#: How recently `/next`/`/result` must have been called for the service to
#: be considered "running" rather than "stalled". A bit more than 2x the
#: default 30s poll interval, to tolerate a slow evaluation or a missed tick
#: without flapping.
_ACTIVITY_STALE_AFTER = timedelta(seconds=90)

#: Minimum gap between recorded queue-depth snapshots. Sampled as a side
#: effect of `/next` (called by every worker every poll interval regardless
#: of where it runs), throttled in-memory so concurrent/frequent pollers
#: don't turn this into an extra query per poll.
_QUEUE_SNAPSHOT_INTERVAL = timedelta(minutes=5)

# In-memory activity tracking for `AdminService.get_llm_service_status()`.
# The app runs as a single gunicorn worker process (see Dockerfile), so a
# module-level timestamp is sufficient — no heartbeat table/DB writes needed.
# This works identically whether the worker calling these endpoints runs on
# the same host or remotely, since it's activity on the endpoints themselves,
# not a separate signal from a specific caller.
_last_next_call_at: datetime | None = None
_last_result_submitted_at: datetime | None = None
_last_queue_snapshot_at: datetime | None = None

# In-memory quota-pause report from the worker. Any worker instance — the
# in-cluster continuous service or a one-off run from a developer's laptop —
# can report this via `POST /api/eval/quota-pause`, since they typically
# share the same OpenRouter account/quota. Reported over HTTP (like all
# other worker activity here) rather than assumed from silence, so the
# admin page can show *why* the worker looks idle instead of just "stalled".
_quota_paused_until: datetime | None = None


def _is_local_caller(key: str) -> bool:
    """Return whether *key* (the caller's IP) counts as "local" for rate limits.

    The continuous `evaluate` worker runs in its own container on the shared
    `vps-net` Podman network (see docker-compose.llm-evaluate.yml) and calls
    craft-dashboard by its container hostname, never over loopback — so its
    source IP is a private container address (e.g. ``10.89.0.x``), not
    ``127.0.0.1``. A literal-loopback check would misclassify it as an
    external caller, throttling it to 30/minute under `--concurrency > 1`,
    which can back the worker off long enough for a `/next` lock to expire
    and the same issue to be picked up and evaluated twice. Any private
    (RFC 1918/RFC 4193/loopback) address is treated as local instead, since
    only same-host/same-network containers can present one here.
    """
    try:
        return ipaddress.ip_address(key).is_private
    except ValueError:
        return False


def _eval_next_rate_limit(key: str) -> str:
    """Return a higher `/next` rate limit for local callers.

    ``slowapi`` calls this with the resolved rate-limit key (the result of
    ``key_func``, i.e. the caller's IP) when the decorated limit value is a
    callable declaring a ``key`` parameter, letting the limit vary per caller
    without needing to inspect the request directly.
    """
    return "1000/minute" if _is_local_caller(key) else "30/minute"


def get_eval_activity() -> tuple[datetime | None, datetime | None]:
    """Return (last `/next` call time, last `/result` submission time).

    Used by ``AdminService.get_llm_service_status`` to derive whether the
    continuous evaluation worker looks like it's actually running, without
    reaching into this module's internals directly.
    """
    return _last_next_call_at, _last_result_submitted_at


def get_quota_pause_until() -> datetime | None:
    """Return when the worker last reported it would resume after a quota pause.

    Returns ``None`` once that time has passed, so a stale report from hours
    ago can't linger and misreport a since-recovered worker as paused.
    """
    if _quota_paused_until is not None and datetime.now(tz=UTC) >= _quota_paused_until:
        return None
    return _quota_paused_until


async def _maybe_record_queue_snapshot(
    session: AsyncSession, *, filtered_issues: dict[str, list[str]] | None
) -> None:
    """Record a queue-depth snapshot, throttled to at most once per interval.

    Called from `/next` — hit by every worker (in-cluster or a developer's
    laptop) on every poll — so queue depth history accumulates automatically
    whenever the worker is running, without a separate cron sampler.
    """
    global _last_queue_snapshot_at  # noqa: PLW0603
    now = datetime.now(tz=UTC)
    if (
        _last_queue_snapshot_at is not None
        and now - _last_queue_snapshot_at < _QUEUE_SNAPSHOT_INTERVAL
    ):
        return
    _last_queue_snapshot_at = now

    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    excl = _build_excluded_issues_condition(filtered_issues or {})

    base_query = select(Issue.id).where(Issue.state == "open")
    if excl is not None:
        base_query = base_query.where(excl)
    total_open = await session.scalar(
        select(func.count()).select_from(base_query.subquery())
    )

    evaluated_today = await session.scalar(
        select(func.count(LLMEvaluation.id)).where(
            LLMEvaluation.evaluated_at >= today_midnight,
            LLMEvaluation.model_name != "pending",
        )
    )

    # Mirrors `eval_status`'s default (open_only=True, force=False,
    # incomplete=False, stale_days=0) pending-count branch — the
    # steady-state queue depth, which is what's meaningful to chart over
    # time. Duplicated rather than shared because `eval_status` is
    # parameterized for ad hoc queries the snapshot doesn't need.
    latest_evaluation = aliased(LLMEvaluation)
    pending_query = (
        select(Issue.id)
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            latest_evaluation,
            (latest_evaluation.issue_id == Issue.id) & latest_evaluation.latest,
        )
        .where(Issue.state == "open")
    )
    if excl is not None:
        pending_query = pending_query.where(excl)
    old_version = or_(
        latest_evaluation.eval_version.is_(None),
        latest_evaluation.eval_version != CURRENT_EVAL_VERSION,
    )
    old_version_unlocked = old_version & or_(
        latest_evaluation.eval_locked_until.is_(None),
        latest_evaluation.eval_locked_until <= now,
    )
    pending_query = pending_query.where(
        or_(
            latest_evaluation.id.is_(None),
            old_version_unlocked,
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

    session.add(
        EvalQueueSnapshot(
            captured_at=now,
            pending_count=pending or 0,
            total_open=total_open or 0,
            evaluated_today=evaluated_today or 0,
        )
    )
    await session.commit()


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
    # Every evaluation includes an embedding — there is no more deferred
    # embedding step, so this is required, not optional.
    summary_embedding: list[float]


def _require_eval_auth(request: Request, authorization: str = "") -> None:
    """Require a valid eval API bearer token."""
    eval_api_token = request.app.state.settings.eval_api_token
    verify_eval_token(authorization, eval_api_token)


def _current_content_hash(issue: Issue) -> str:
    """Return the issue's current content hash.

    Prefers the denormalized ``Issue.content_hash`` (kept up to date by the
    collectors on every create/update), so this is an O(1) column read
    instead of recomputing a hash from title/body/comments/pr_details on
    every call. Falls back to computing it on the fly if somehow unset (e.g.
    a race with the one-time backfill script for pre-existing issues).
    """
    if issue.content_hash:
        return issue.content_hash
    return compute_content_hash(
        issue.title,
        issue.body,
        issue.state,
        issue.labels or [],
        issue.comments or [],
        pr_details=issue.metadata_ or None,
    )


async def _fetch_issue_and_latest_evaluation(
    session: AsyncSession,
    *,
    project: str = "",
    open_only: bool = True,
    force: bool = False,
    incomplete: bool = False,
    stale_days: int = 0,
    external_id: str = "",
    filtered_issues: dict[str, list[str]] | None = None,
) -> tuple[Issue, str, LLMEvaluation | None] | None:
    query = build_pending_evaluation_query(
        project=project,
        open_only=open_only,
        force=force,
        incomplete=incomplete,
        stale_days=stale_days,
        external_id=external_id,
        filtered_issues=filtered_issues,
    ).limit(1)

    result = await session.execute(query)
    row = result.first()
    if row is None:
        return None

    issue, project_name, evaluation = row[0], row[1], row[2]
    # Log a warning when re-evaluating an issue that was recently evaluated,
    # to make unexpected re-evaluations visible in the server logs.
    if (
        evaluation is not None
        and evaluation.evaluated_at is not None
        and evaluation.model_name != "pending"
    ):
        age_minutes = (
            datetime.now(tz=UTC) - evaluation.evaluated_at
        ).total_seconds() / 60
        if age_minutes < 120:  # noqa: PLR2004
            logger.warning(
                "Re-evaluating %s/%s (evaluated %.0f min ago, "
                "stored_hash=%s, current_hash=%s, version=%s)",
                project_name,
                issue.external_id,
                age_minutes,
                evaluation.issue_data_hash,
                _current_content_hash(issue),
                evaluation.eval_version,
            )
    return issue, project_name, evaluation


@router.get("/next", response_model=None)
@limiter.limit(_eval_next_rate_limit)
async def next_issue(
    request: Request,
    *,
    authorization: str = Header(default=""),
    project: str = Query(default=""),
    open_only: bool = Query(default=True),
    force: bool = Query(default=False),
    incomplete: bool = Query(default=False),
    stale_days: int = Query(default=0),
    external_id: str = Query(
        default="", description="Evaluate only this issue/PR number (requires project)"
    ),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any] | Response:
    """Return the next issue that needs evaluation, if any."""
    global _last_next_call_at  # noqa: PLW0603
    _require_eval_auth(request, authorization)
    _last_next_call_at = datetime.now(tz=UTC)
    await _maybe_record_queue_snapshot(
        session, filtered_issues=get_config(request).filtered_issues
    )

    if external_id and not project:
        raise HTTPException(
            status_code=422, detail="external_id requires project to be set"
        )

    row = await _fetch_issue_and_latest_evaluation(
        session,
        project=project,
        # A single targeted issue may be closed/merged or already evaluated;
        # don't let the open/hash filters silently skip it.
        open_only=open_only if not external_id else False,
        force=force or bool(external_id),
        incomplete=incomplete,
        stale_days=stale_days,
        external_id=external_id,
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

    try:
        await session.commit()
    except IntegrityError:
        # ``FOR UPDATE SKIP LOCKED`` in build_pending_evaluation_query makes
        # this rare, but doesn't eliminate it entirely: a concurrent worker
        # can commit its own claim on this same never-before-evaluated issue
        # in the narrow window between our SELECT and our INSERT. Treat that
        # exactly like "no work available" instead of a 500 — the other
        # worker already claimed it, so there's nothing for this caller to
        # do but poll again.
        await session.rollback()
        return Response(status_code=204)

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
        "closing_references": (issue.metadata_ or {}).get("closing_references", []),
        # PR review/CI/diff metadata (review_status, ci_passing/ci_failing/
        # ci_pending, diff stats, etc.), populated by the collector for pull
        # requests. Empty for plain issues. Forwarded so the eval client can
        # give the LLM review/CI context and keep its local hash check
        # (which also hashes a subset of these fields) in sync with the server.
        "pr_details": issue.metadata_ or {},
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
    global _last_result_submitted_at  # noqa: PLW0603
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
            LLMEvaluation.latest,
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
    _last_result_submitted_at = datetime.now(tz=UTC)
    return {"status": "stored", "issue_id": payload.issue_id}


class QuotaPauseReport(BaseModel):
    """Request body for a worker reporting it has entered a quota backoff."""

    resume_at: datetime
    reason: str = "quota"


@router.post("/quota-pause")
async def report_quota_pause(
    request: Request,
    payload: QuotaPauseReport,
    *,
    authorization: str = Header(default=""),
) -> dict[str, str]:
    """Record that a worker has paused evaluation until an LLM quota resets.

    Any worker instance can call this — the in-cluster continuous service or
    a one-off run from a developer's laptop — since they typically draw on
    the same OpenRouter account/quota, so either one hitting the daily/rate
    limit is equally relevant to "is evaluation making progress right now".
    The admin page surfaces this as "stalled (quota reached)" instead of a
    bare "stalled", which would otherwise be indistinguishable from a
    genuinely broken worker.
    """
    global _quota_paused_until  # noqa: PLW0603
    _require_eval_auth(request, authorization)
    _quota_paused_until = payload.resume_at
    return {"status": "recorded"}


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
            LLMEvaluation.latest,
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
                (latest_evaluation.issue_id == Issue.id) & latest_evaluation.latest,
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
            old_version = or_(
                latest_evaluation.eval_version.is_(None),
                latest_evaluation.eval_version != CURRENT_EVAL_VERSION,
            )
            old_version_unlocked = old_version & or_(
                latest_evaluation.eval_locked_until.is_(None),
                latest_evaluation.eval_locked_until <= now,
            )
            pending_query = pending_query.where(
                or_(
                    latest_evaluation.id.is_(None),
                    old_version_unlocked,
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

    return {
        "pending": pending or 0,
        "locked": locked or 0,
        "evaluated_today": evaluated_today or 0,
        "total_evaluated": total_evaluated or 0,
        "total_open": total_open or 0,
    }
