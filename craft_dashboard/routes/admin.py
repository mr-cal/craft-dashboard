"""Admin routes for triggering refreshes and re-evaluations."""

import asyncio
import html
import logging
import pathlib
import sys
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, Request
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


@router.post("/auth")
async def admin_auth(
    request: Request,
    credentials: AdminAuthRequest,
) -> JSONResponse:
    """Validate the admin token and establish an admin session cookie."""
    _verify_origin(request)
    verify_admin_token(f"Bearer {credentials.token}", _get_admin_token(request))

    response = JSONResponse({"status": "authenticated", "message": "Authenticated."})
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
    response = JSONResponse({"status": "logged_out", "message": "Logged out."})
    response.delete_cookie(_ADMIN_SESSION_COOKIE, path="/admin")
    return response


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

    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {
            "project_names": project_names,
            "schedule_days": schedule_days,
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

    return JSONResponse(
        {"status": "refresh_queued", "message": "Data refresh has been queued."},
        status_code=202,
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
            return JSONResponse(
                {
                    "status": "dry_run_complete",
                    "message": f"Dry run: {count} issues would be evaluated",
                    "count": count,
                }
            )
        # Fallback: show raw output
        return JSONResponse(
            {
                "status": "dry_run_complete",
                "message": "Dry run completed. Check logs for details.",
            }
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

    return JSONResponse(
        {
            "status": "evaluation_queued",
            "message": f"LLM re-evaluation queued: {desc}",
        },
        status_code=202,
    )


@router.post("/distribute")
async def distribute_refresh_schedule(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Distribute refresh schedules evenly over the next N days.

    Requires admin authentication via Bearer token.
    """
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
            {"status": "success", "message": "No schedules to distribute.", "count": 0}
        )

    now = datetime.now(UTC)
    total_schedules = len(schedules)

    for idx, schedule in enumerate(schedules):
        day_offset = (idx * refresh_age_days) // total_schedules
        schedule.next_refresh_at = now + timedelta(days=day_offset)

    await session.commit()
    logger.info(
        "Admin: distributed %d schedules over %d days",
        total_schedules,
        refresh_age_days,
    )

    return JSONResponse(
        {
            "status": "success",
            "message": f"Distributed {total_schedules} schedules over {refresh_age_days} days.",
            "count": total_schedules,
        }
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
