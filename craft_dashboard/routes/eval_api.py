"""Eval API routes for pull-based issue evaluation."""

from __future__ import annotations

import ipaddress
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from scripts.llm.validation import validate_evaluation_result
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.auth import verify_eval_token
from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.git_mirrors import reader as mirror_reader
from craft_dashboard.llm.client import OPENROUTER_BASE_URL
from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.llm.evaluation_queue import build_pending_evaluation_query
from craft_dashboard.llm.evaluator import (
    CURRENT_EVAL_VERSION,
    current_version_for_state,
    expected_version_sql_expr,
)
from craft_dashboard.llm.exceptions import LLMValidationError
from craft_dashboard.models.commit_scan_evidence_path import CommitScanEvidencePath
from craft_dashboard.models.eval_queue_snapshot import EvalQueueSnapshot
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.repositories.issue_repository import (
    IssueRepository,
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
_RELEASED_MODEL_PREFIX = "released:"
_PREFLIGHT_RELEASE_PREFIX = "released:preflight"


class EvalReleaseRequest(BaseModel):
    """Request body for releasing a claimed issue without an evaluation."""

    issue_id: int
    reason: str


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

    base_query = (
        select(Issue.id)
        .join(Project, Issue.project_id == Project.id)
        .where(Issue.state == "open")
    )
    if excl is not None:
        base_query = base_query.where(excl)
    total_open = await session.scalar(
        select(func.count()).select_from(base_query.subquery())
    )

    evaluated_today = await session.scalar(
        select(func.count(LLMEvaluation.id)).where(
            LLMEvaluation.evaluated_at >= today_midnight,
            LLMEvaluation.model_name != "pending",
            ~LLMEvaluation.model_name.like(f"{_RELEASED_MODEL_PREFIX}%"),
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
    # Actual billed USD cost reported by the backend, if any. None for
    # backends that don't report cost (e.g. the local LLM server).
    cost_usd: float | None = None
    # Every evaluation includes an embedding — there is no more deferred
    # embedding step, so this is required, not optional.
    summary_embedding: list[float]
    # Embedding of the issue's title+body (not the LLM summary), used for
    # semantic issue search. Stored on Issue.search_embedding rather than
    # LLMEvaluation, since it describes the issue's content, not this
    # particular evaluation. Required for the same reason as
    # summary_embedding above — computed unconditionally by the worker
    # alongside the summary embedding.
    search_embedding: list[float]
    evidence_paths: list[dict[str, str]] = Field(default_factory=list)


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


def _normalize_evidence_paths(
    evidence_paths: list[dict[str, str]],
) -> list[tuple[str, str]]:
    """Return distinct ``(project, path)`` pairs for reverse-index storage."""
    pairs = {
        (
            # Task 5 recorded qualified owner/repo strings like
            # ``canonical/rockcraft`` in ``ctx.touched_paths``, but the
            # reverse-index table stores the short project name used
            # throughout commit scanning (for example ``rockcraft``).
            path["repo"].rsplit("/", 1)[-1],
            path["path"],
        )
        for path in evidence_paths
    }
    return sorted(pairs)


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


async def _resolve_repo_shas(
    *,
    project_name: str,
    mirror_dir: Path,
    craft_applications: list[str],
    craft_libraries: list[str],
    craft_consumers: list[str],
) -> dict[str, str]:
    """Return {project: head_sha} for the round-1 baseline's repo set.

    Per the design doc's round-1 scope table: if project_name is one of the
    craft-applications (or craft-application itself), include its own repo
    plus every craft-library; otherwise include only its own repo. A
    project listed in craft-consumers (e.g. "snapcraft-rocks", which builds
    rock images packaging multiple craft-applications) additionally gets
    every craft-application's repo, on top of the libraries every app gets.
    Silently omits any project with no mirror on disk yet (the worker's
    preflight step is responsible for deciding whether that's fatal).
    """
    repos_needed = {project_name}
    if project_name in craft_applications or project_name == "craft-application":
        repos_needed |= set(craft_libraries)
    if project_name in craft_consumers:
        repos_needed |= set(craft_libraries) | set(craft_applications)

    shas: dict[str, str] = {}
    for repo in repos_needed:
        mirror_path = mirror_dir / f"{repo}.git"
        if not mirror_path.exists():
            continue
        try:
            sha = await mirror_reader.head_sha(mirror_path)
        except Exception:  # noqa: BLE001 - a corrupt/empty mirror must not break /next
            continue
        shas[repo] = sha
    return shas


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

    settings = request.app.state.settings
    dashboard_config = get_config(request)
    repo_shas = await _resolve_repo_shas(
        project_name=project_name,
        mirror_dir=settings.mirror_dir_path,
        craft_applications=list(dashboard_config.craft_applications),
        craft_libraries=list(dashboard_config.craft_libraries),
        craft_consumers=list(dashboard_config.craft_consumers),
    )

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
        "repo_shas": repo_shas,
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
    issue.search_embedding = payload.search_embedding
    session.add(
        LLMEvaluation(
            issue_id=payload.issue_id,
            model_name=payload.model_used,
            eval_version=current_version_for_state(issue.state),
            summary=payload.summary,
            suggested_action=payload.suggested_action,
            suggested_action_reason=payload.suggested_action_reason,
            scores=payload.scores,
            tokens_used=payload.tokens_used,
            prompt_tokens=payload.prompt_tokens,
            completion_tokens=payload.completion_tokens,
            llm_backend=payload.llm_backend,
            cost_usd=payload.cost_usd,
            evaluated_at=datetime.now(tz=UTC),
            issue_data_hash=current_hash,
            evidence_generation=issue.evidence_generation,
            latest=True,
            eval_locked_until=datetime.now(tz=UTC) + _LOCK_TTL,
            summary_embedding=payload.summary_embedding,
        )
    )
    await session.execute(
        delete(CommitScanEvidencePath).where(
            CommitScanEvidencePath.issue_id == payload.issue_id
        )
    )
    session.add_all(
        [
            CommitScanEvidencePath(
                issue_id=payload.issue_id,
                project=project,
                path=path,
            )
            for project, path in _normalize_evidence_paths(payload.evidence_paths)
        ]
    )

    await session.commit()
    _last_result_submitted_at = datetime.now(tz=UTC)
    return {"status": "stored", "issue_id": payload.issue_id}


@router.post("/release")
async def release_claim(
    request: Request,
    payload: EvalReleaseRequest,
    *,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Release a claimed issue without recording an LLM evaluation."""
    _require_eval_auth(request, authorization)

    latest_evaluation = await session.scalar(
        select(LLMEvaluation).where(
            LLMEvaluation.issue_id == payload.issue_id,
            LLMEvaluation.latest,
        )
    )
    if latest_evaluation is None:
        raise HTTPException(status_code=404, detail="Claim not found")

    latest_evaluation.eval_locked_until = None
    if latest_evaluation.model_name == "pending":
        latest_evaluation.latest = False

    session.add(
        LLMEvaluation(
            issue_id=payload.issue_id,
            model_name=f"{_RELEASED_MODEL_PREFIX}{payload.reason}",
            latest=False,
            eval_locked_until=None,
        )
    )
    await session.commit()
    return {"status": "released", "issue_id": payload.issue_id}


@router.get("/related")
async def related_issues(
    request: Request,
    *,
    authorization: str = Header(default=""),
    issue_id: int = Query(...),
    query: str = Query(..., max_length=1000),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return issues whose latest summary embeddings are closest to query."""
    _require_eval_auth(request, authorization)
    settings = request.app.state.settings
    if not settings.openrouter_api_key:
        raise HTTPException(
            status_code=503,
            detail="Embedding service unavailable",
        )
    embed_client = EmbeddingClient(
        base_url=OPENROUTER_BASE_URL,
        model=settings.semantic_search_embedding_model,
        api_key=settings.openrouter_api_key,
        ca_cert="",
    )
    try:
        try:
            query_embedding = await embed_client.embed(query, dimensions=1024)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Related-issues embedding failed for query",
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="Embedding service unavailable",
            ) from None
    finally:
        await embed_client.close()

    repo = IssueRepository(session, filtered_issues=get_config(request).filtered_issues)
    results = await repo.find_related_by_summary_embedding(
        query_embedding=query_embedding,
        exclude_issue_id=issue_id,
        top_n=settings.related_issues_top_n,
        similarity_threshold=settings.related_issues_similarity_threshold,
    )
    return {"results": results}


@router.get("/issue")
async def issue_detail_lookup(
    request: Request,
    *,
    authorization: str = Header(default=""),
    ref: str = Query(..., max_length=200),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Resolve a repo issue reference for the eval issue-detail tool."""
    _require_eval_auth(request, authorization)
    project_name, external_id = _parse_issue_ref(ref)

    query_stmt = (
        select(
            Issue.external_id,
            Issue.title,
            Issue.state,
            Issue.url,
            Project.name.label("project_name"),
            LLMEvaluation.summary,
        )
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest,
        )
        .where(Issue.external_id == external_id)
    )
    excl = _build_excluded_issues_condition(get_config(request).filtered_issues)
    if excl is not None:
        query_stmt = query_stmt.where(excl)
    if project_name:
        query_stmt = query_stmt.where(Project.name == project_name)

    result = await session.execute(
        query_stmt.order_by(Project.name.asc(), Issue.id.asc())
    )
    candidates = [
        {
            "project_name": row.project_name,
            "external_id": row.external_id,
            "title": row.title,
            "summary": row.summary,
            "state": row.state,
            "url": row.url,
        }
        for row in result.all()
    ]
    return {"candidates": candidates}


def _parse_issue_ref(ref: str) -> tuple[str | None, str]:
    """Split an issue ref into (project_name_or_none, external_id)."""
    stripped = ref.strip()
    if stripped.startswith("#"):
        return None, stripped.lstrip("#")
    if "#" not in stripped:
        return None, stripped

    prefix, external_id = stripped.rsplit("#", 1)
    if "/" in prefix:
        prefix = prefix.rsplit("/", 1)[-1]
    return (prefix or None), external_id


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
    latest_eval_ids = (
        select(
            LLMEvaluation.issue_id,
            func.max(LLMEvaluation.id).label("latest_eval_id"),
        )
        .group_by(LLMEvaluation.issue_id)
        .subquery()
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
            ~LLMEvaluation.model_name.like(f"{_RELEASED_MODEL_PREFIX}%"),
        )
    )
    total_evaluated = await session.scalar(
        select(func.count(LLMEvaluation.id)).where(
            LLMEvaluation.latest,
            LLMEvaluation.model_name != "pending",
            ~LLMEvaluation.model_name.like(f"{_RELEASED_MODEL_PREFIX}%"),
        )
    )
    preflight_blocked_query = (
        select(func.count(LLMEvaluation.id))
        .join(
            latest_eval_ids,
            (latest_eval_ids.c.issue_id == LLMEvaluation.issue_id)
            & (latest_eval_ids.c.latest_eval_id == LLMEvaluation.id),
        )
        .join(Issue, LLMEvaluation.issue_id == Issue.id)
        .join(Project, Issue.project_id == Project.id)
        .where(LLMEvaluation.model_name.like(f"{_PREFLIGHT_RELEASE_PREFIX}%"))
    )
    if open_only:
        preflight_blocked_query = preflight_blocked_query.where(Issue.state == "open")
    if project:
        preflight_blocked_query = preflight_blocked_query.where(Project.name == project)
    if excl is not None:
        preflight_blocked_query = preflight_blocked_query.where(excl)
    preflight_blocked = await session.scalar(preflight_blocked_query)

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
            expected_version = expected_version_sql_expr()
            old_version = or_(
                latest_evaluation.eval_version.is_(None),
                latest_evaluation.eval_version != expected_version,
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
        "preflight_blocked": preflight_blocked or 0,
        "evaluated_today": evaluated_today or 0,
        "total_evaluated": total_evaluated or 0,
        "total_open": total_open or 0,
    }
