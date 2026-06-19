"""Admin routes for triggering refreshes and re-evaluations."""

import asyncio
import html
import json
import logging
import pathlib
import sys
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status
from starlette.exceptions import HTTPException

from craft_dashboard.auth import get_admin_bearer_token, verify_admin_token
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.services import AdminService

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

_ADMIN_SESSION_COOKIE = "admin_session"
_LOG_SERVICE_UNITS: list[str] = ["collect-data", "craft-dashboard", "run-llm"]


class AdminActionResponse(BaseModel):
    """Response model for admin action endpoints."""

    status: str
    message: str
    count: int | None = None


class AdminAuthRequest(BaseModel):
    """Request model for admin cookie authentication."""

    token: str


class ReEvaluateRequest(BaseModel):
    """Request model for re-evaluation parameters."""

    project: str = ""
    limit: int = 0
    stale_days: int = 0
    force: bool = False
    incomplete: bool = False
    open_only: bool = True
    dry_run: bool = False


def _get_admin_token(request: Request) -> str:
    """Get the admin token from app settings."""
    return request.app.state.settings.admin_token


def _verify_origin(request: Request) -> None:
    """Verify request originates from dashboard UI."""
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if origin and urllib.parse.urlparse(origin).netloc != host:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin request rejected.",
        )


def _require_admin_auth(request: Request, authorization: str = "") -> None:
    """Verify admin auth from the Authorization header or session cookie."""
    admin_token = _get_admin_token(request)
    token = get_admin_bearer_token(request, authorization)
    verify_admin_token(token, admin_token)


def _build_toast_headers(message: str, toast_type: str) -> dict[str, str]:
    """Build response headers that trigger a client-side toast."""
    return {
        "HX-Trigger": json.dumps(
            {"toast": {"message": message, "type": toast_type}},
            separators=(",", ":"),
        )
    }


@router.post("/auth")
async def admin_auth(
    request: Request,
    credentials: AdminAuthRequest,
) -> JSONResponse:
    """Validate the admin token and establish an admin session cookie."""
    _verify_origin(request)
    verify_admin_token(f"Bearer {credentials.token}", _get_admin_token(request))

    response = JSONResponse(
        {"status": "authenticated", "message": "Authenticated."},
        headers=_build_toast_headers("Authenticated.", "success"),
    )
    response.set_cookie(
        _ADMIN_SESSION_COOKIE,
        credentials.token,
        httponly=True,
        samesite="strict",
        path="/admin",
    )
    return response


@router.post("/logout")
async def admin_logout(request: Request) -> JSONResponse:
    """Clear the admin session cookie."""
    _verify_origin(request)
    response = JSONResponse(
        {"status": "logged_out", "message": "Logged out."},
        headers=_build_toast_headers("Logged out.", "info"),
    )
    response.delete_cookie(_ADMIN_SESSION_COOKIE, path="/admin")
    return response


@router.get("/status", response_class=JSONResponse)
async def collection_status(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Return current collection/evaluation status."""
    admin_service = AdminService(session)
    status_payload = await admin_service.get_system_status()
    request.state.system_status = status_payload
    return JSONResponse(jsonable_encoder(status_payload))


@router.get("", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the admin dashboard page."""
    templates: Jinja2Templates = request.app.state.templates

    admin_service = AdminService(session)
    project_names = await admin_service.get_project_names()
    schedule_days = await admin_service.get_schedule_day_counts()
    lifetime_stats = await admin_service.get_lifetime_token_stats()
    recent_stats = await admin_service.get_seven_day_token_stats()
    collection_runs = await admin_service.get_recent_collection_runs()
    next_refresh = await admin_service.get_next_scheduled_refresh()

    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {
            "project_names": project_names,
            "schedule_days": schedule_days,
            "next_refresh": next_refresh,
            "total_evaluations": lifetime_stats["evaluations"],
            "total_tokens": lifetime_stats["tokens"],
            "total_prompt_tokens": lifetime_stats["prompt_tokens"],
            "total_completion_tokens": lifetime_stats["completion_tokens"],
            "recent_evaluations": recent_stats["evaluations"],
            "recent_tokens": recent_stats["tokens"],
            "recent_prompt_tokens": recent_stats["prompt_tokens"],
            "recent_completion_tokens": recent_stats["completion_tokens"],
            "collection_runs": collection_runs,
        },
    )


@router.post("/refresh")
async def trigger_refresh(
    request: Request,
    authorization: str = Header(default=""),
    _session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Trigger a data refresh for all projects.

    Requires admin authentication via Bearer token.
    """
    _require_admin_auth(request, authorization)
    _verify_origin(request)
    logger.info("Admin: refresh queued")

    cmd = [
        sys.executable,
        str(
            pathlib.Path(__file__).resolve().parent.parent.parent  # noqa: ASYNC240
            / "scripts"
            / "collect_data.py"
        ),
    ]
    asyncio.create_task(
        asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    )

    return JSONResponse(
        {"status": "refresh_queued", "message": "Data refresh has been queued."},
        status_code=202,
        headers=_build_toast_headers("Data refresh has been queued.", "success"),
    )


@router.post("/re-evaluate")
async def trigger_re_evaluation(
    request: Request,
    body: ReEvaluateRequest | None = None,
    authorization: str = Header(default=""),
    _session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Trigger LLM re-evaluation of issues with optional parameters.

    Requires admin authentication via Bearer token.
    """
    _require_admin_auth(request, authorization)
    _verify_origin(request)

    settings = request.app.state.settings
    if not settings.enable_server_eval:
        message = (
            "Server-side evaluation is disabled (ENABLE_SERVER_EVAL=false). "
            "Use the eval client script for pull-based evaluation."
        )
        return JSONResponse(
            {"status": "disabled", "message": message},
            status_code=status.HTTP_409_CONFLICT,
            headers=_build_toast_headers(message, "warning"),
        )

    params = body or ReEvaluateRequest()

    # Build command for run_llm.py
    cmd = [
        sys.executable,
        str(
            pathlib.Path(__file__).resolve().parent.parent.parent  # noqa: ASYNC240
            / "scripts"
            / "run_llm.py"
        ),
        "evaluate",
    ]
    if params.project:
        cmd.extend(["--project", params.project])
    if params.limit > 0:
        cmd.extend(["--limit", str(params.limit)])
    if params.stale_days > 0:
        cmd.extend(["--stale-days", str(params.stale_days)])
    if params.force:
        cmd.append("--force")
    if params.incomplete:
        cmd.append("--incomplete")
    if params.open_only:
        cmd.append("--open-only")
    if params.dry_run:
        cmd.append("--dry-run")

    logger.info("Admin: re-evaluation triggered with params: %s", params.model_dump())

    if params.dry_run:
        # Run synchronously to capture the count
        import re

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode() if stdout else ""
        # Parse the "DRY RUN: N issues would be evaluated" line from output
        match = re.search(r"DRY RUN:\s*(\d+)\s*issues would be evaluated", output)
        count = int(match.group(1)) if match else None

        if count is not None:
            message = f"Dry run: {count} issues would be evaluated"
            return JSONResponse(
                {
                    "status": "dry_run_complete",
                    "message": message,
                    "count": count,
                },
                headers=_build_toast_headers(message, "info"),
            )
        # Fallback: show raw output
        return JSONResponse(
            {
                "status": "dry_run_complete",
                "message": "Dry run completed. Check logs for details.",
            },
            headers=_build_toast_headers(
                "Dry run completed. Check logs for details.", "info"
            ),
        )
    # Run in background
    asyncio.create_task(
        asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    )

    desc_parts = []
    if params.project:
        desc_parts.append(f"project={params.project}")
    if params.limit > 0:
        desc_parts.append(f"limit={params.limit}")
    if params.stale_days > 0:
        desc_parts.append(f"stale_days={params.stale_days}")
    if params.force:
        desc_parts.append("force")
    if params.incomplete:
        desc_parts.append("incomplete")
    desc = ", ".join(desc_parts) or "all open issues"

    message = f"LLM re-evaluation queued: {desc}"
    return JSONResponse(
        {
            "status": "evaluation_queued",
            "message": message,
        },
        status_code=202,
        headers=_build_toast_headers(
            f"LLM re-evaluation has been queued for {desc}.", "success"
        ),
    )


@router.post("/distribute")
async def distribute_refresh_schedule(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Distribute refresh schedules evenly over the next N days by issue count.

    Projects with more issues are spaced further apart so that each unit of
    time processes roughly the same number of issues, rather than giving each
    project an equal time slot regardless of its size.

    Requires admin authentication via Bearer token.
    """
    from sqlalchemy import func

    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.refresh_schedule import (
        RefreshSchedule,
    )

    _require_admin_auth(request, authorization)
    _verify_origin(request)

    settings = request.app.state.settings
    refresh_age_days = settings.refresh_age_days

    result = await session.execute(select(RefreshSchedule))
    schedules = list(result.scalars())

    if not schedules:
        return JSONResponse(
            {"status": "success", "message": "No schedules to distribute.", "count": 0},
            headers=_build_toast_headers(
                "Refresh schedule redistributed for 0 projects.", "success"
            ),
        )

    # Get issue count per (project_id, source) so we can weight by workload.
    issue_counts_result = await session.execute(
        select(
            Issue.project_id,
            Issue.source,
            func.count(Issue.id).label("issue_count"),
        ).group_by(Issue.project_id, Issue.source)
    )
    issue_counts: dict[tuple[int, str], int] = {
        (row.project_id, row.source): row.issue_count for row in issue_counts_result
    }

    # Assign each schedule a weight (minimum 1 so new projects still get a slot).
    weights = [max(1, issue_counts.get((s.project_id, s.source), 0)) for s in schedules]

    # Greedy bin packing: sort heaviest-first, assign each to the lightest day.
    # This minimises the maximum-day load and gives the most even distribution.
    order = sorted(range(len(schedules)), key=lambda i: -weights[i])
    day_totals = [0] * refresh_age_days
    day_assignments: list[list[int]] = [[] for _ in range(refresh_age_days)]

    for orig_idx in order:
        lightest_day = min(range(refresh_age_days), key=lambda d: day_totals[d])
        day_assignments[lightest_day].append(orig_idx)
        day_totals[lightest_day] += weights[orig_idx]

    # Use midnight-aligned day boundaries so assignments match the calendar-day
    # buckets that get_schedule_day_counts uses.  Start from tomorrow so nothing
    # is scheduled in tonight's remaining hours.
    now = datetime.now(UTC)
    tomorrow_midnight = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    for day_idx, assigned_indices in enumerate(day_assignments):
        n = len(assigned_indices)
        day_start = tomorrow_midnight + timedelta(days=day_idx)
        for slot, orig_idx in enumerate(assigned_indices):
            schedules[orig_idx].next_refresh_at = day_start + timedelta(
                seconds=86400 * (slot + 1) / (n + 1)
            )

    await session.commit()
    total_schedules = len(schedules)
    logger.info(
        "Admin: distributed %d schedules over %d days (weighted by issue count)",
        total_schedules,
        refresh_age_days,
    )

    return JSONResponse(
        {
            "status": "success",
            "message": f"Distributed {total_schedules} schedules over {refresh_age_days} days.",
            "count": total_schedules,
        },
        headers=_build_toast_headers(
            f"Refresh schedule redistributed for {total_schedules} projects.",
            "success",
        ),
    )


@router.get("/health")
async def admin_health(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Detailed health check: DB connectivity and collection status."""
    from sqlalchemy import select, text

    from craft_dashboard.models.refresh_schedule import (
        RefreshSchedule,
    )

    _require_admin_auth(request, authorization)

    try:
        await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        logger.exception("Health check: database connection failed")
        db_status = "error"

    result = await session.execute(
        select(RefreshSchedule)
        .where(RefreshSchedule.consecutive_failures > 0)
        .order_by(RefreshSchedule.consecutive_failures.desc())
    )
    failing = [
        {
            "project_id": row.project_id,
            "source": row.source,
            "consecutive_failures": row.consecutive_failures,
            "last_error": row.last_error,
            "last_refreshed_at": (
                row.last_refreshed_at.isoformat() if row.last_refreshed_at else None
            ),
        }
        for row in result.scalars()
    ]

    return JSONResponse(
        {
            "status": "ok" if db_status == "ok" else "degraded",
            "database": db_status,
            "failing_collectors": failing,
        }
    )


@router.get("/logs", response_class=PlainTextResponse)
async def admin_logs(
    request: Request,
    authorization: str = Header(default=""),
) -> PlainTextResponse:
    """Return recent service logs. Requires admin auth."""
    _require_admin_auth(request, authorization)

    try:
        units: list[str] = []
        for unit in _LOG_SERVICE_UNITS:
            units.extend(["-u", unit])

        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                "journalctl",
                *units,
                "-n",
                "100",
                "--no-pager",
                "--output",
                "short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=10,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode().strip() if stdout else ""
        if not output or output == "-- No entries --":
            return PlainTextResponse(
                f"(no journal entries found for units: {', '.join(_LOG_SERVICE_UNITS)})\n"
                "Hint: logs are only available when running as systemd services on the deployed server."
            )
        return PlainTextResponse(html.escape(output))
    except (TimeoutError, FileNotFoundError):
        return PlainTextResponse(
            "(journalctl not available — logs are only accessible on the deployed server)"
        )
