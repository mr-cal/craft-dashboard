"""Admin routes for dashboard operations and refreshes."""

import asyncio
import html
import json
import logging
import pathlib
import sys
import urllib.parse
from typing import TYPE_CHECKING

import requests
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from github import GithubException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status
from starlette.exceptions import HTTPException

from craft_dashboard.auth import get_admin_bearer_token, verify_admin_token
from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.services import AdminService

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

_ADMIN_SESSION_COOKIE = "admin_session"
_LOG_SERVICE_UNITS: list[str] = ["collect-data", "craft-dashboard", "run-llm"]
_ADMIN_PAGE_SIZE = 10


class AdminActionResponse(BaseModel):
    """Response model for admin action endpoints."""

    status: str
    message: str
    count: int | None = None


class AdminAuthRequest(BaseModel):
    """Request model for admin cookie authentication."""

    token: str


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
async def admin_overview_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the admin Overview tab: system status, collection runs, recent activity."""
    templates: Jinja2Templates = request.app.state.templates

    admin_service = AdminService(session)
    collection_runs = await admin_service.get_recent_collection_runs(
        filtered_issues=get_config(request).filtered_issues
    )
    (
        recent_activity,
        recent_activity_total,
    ) = await admin_service.get_recent_issue_activity(
        limit=_ADMIN_PAGE_SIZE,
        filtered_issues=get_config(request).filtered_issues,
    )
    try:
        api_budget = await admin_service.get_api_budget()
    except (GithubException, requests.exceptions.RequestException) as exc:
        logger.warning("Admin page: API budget lookup failed: %s", exc)
        api_budget = None
    next_expected_fetch = await admin_service.get_next_expected_fetch()

    return templates.TemplateResponse(
        request,
        "admin/overview.html",
        {
            "collection_runs": collection_runs,
            "recent_activity": recent_activity,
            "recent_activity_offset": 0,
            "recent_activity_limit": _ADMIN_PAGE_SIZE,
            "recent_activity_total": recent_activity_total,
            "api_budget": api_budget,
            "next_expected_fetch": next_expected_fetch,
        },
    )


@router.get("/evaluations", response_class=HTMLResponse)
async def admin_evaluations_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the admin LLM Evaluations tab."""
    templates: Jinja2Templates = request.app.state.templates

    admin_service = AdminService(session)
    lifetime_stats = await admin_service.get_lifetime_token_stats()
    recent_stats = await admin_service.get_seven_day_token_stats()
    llm_service_status = await admin_service.get_llm_service_status()
    (
        llm_recent_evaluations,
        llm_recent_evaluations_total,
    ) = await admin_service.get_recent_evaluations(limit=_ADMIN_PAGE_SIZE)
    llm_daily_stats = await admin_service.get_daily_evaluation_stats()
    llm_queue_depth_history = await admin_service.get_queue_depth_history()
    outdated_evaluation_counts = await admin_service.get_outdated_evaluation_counts(
        filtered_issues=get_config(request).filtered_issues
    )

    return templates.TemplateResponse(
        request,
        "admin/evaluations.html",
        {
            "total_evaluations": lifetime_stats["evaluations"],
            "total_tokens": lifetime_stats["tokens"],
            "total_prompt_tokens": lifetime_stats["prompt_tokens"],
            "total_completion_tokens": lifetime_stats["completion_tokens"],
            "recent_evaluations": recent_stats["evaluations"],
            "recent_tokens": recent_stats["tokens"],
            "recent_prompt_tokens": recent_stats["prompt_tokens"],
            "recent_completion_tokens": recent_stats["completion_tokens"],
            "llm_service_status": llm_service_status,
            "llm_recent_evaluations": llm_recent_evaluations,
            "llm_recent_evaluations_offset": 0,
            "llm_recent_evaluations_limit": _ADMIN_PAGE_SIZE,
            "llm_recent_evaluations_total": llm_recent_evaluations_total,
            "llm_daily_stats": llm_daily_stats,
            "llm_queue_depth_history": llm_queue_depth_history,
            "outdated_evaluation_counts": outdated_evaluation_counts,
        },
    )


@router.get("/schedule", response_class=HTMLResponse)
async def admin_schedule_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the admin Refresh Schedule tab: the hourly rotation order."""
    templates: Jinja2Templates = request.app.state.templates

    admin_service = AdminService(session)
    project_refresh_list = await admin_service.get_project_refresh_list()

    return templates.TemplateResponse(
        request,
        "admin/schedule.html",
        {"project_refresh_list": project_refresh_list},
    )


@router.get("/recent-activity", response_class=HTMLResponse)
async def recent_activity_fragment(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=_ADMIN_PAGE_SIZE, gt=0, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Return an HTML fragment with one page of recent issue activity."""
    templates: Jinja2Templates = request.app.state.templates
    admin_service = AdminService(session)
    recent_activity, total = await admin_service.get_recent_issue_activity(
        limit=limit,
        offset=offset,
        filtered_issues=get_config(request).filtered_issues,
    )
    return templates.TemplateResponse(
        request,
        "admin/_recent_activity_rows.html",
        {
            "recent_activity": recent_activity,
            "recent_activity_offset": offset,
            "recent_activity_limit": limit,
            "recent_activity_total": total,
        },
    )


@router.get("/recent-evaluations", response_class=HTMLResponse)
async def recent_evaluations_fragment(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=_ADMIN_PAGE_SIZE, gt=0, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Return an HTML fragment with one page of recent LLM evaluations."""
    templates: Jinja2Templates = request.app.state.templates
    admin_service = AdminService(session)
    llm_recent_evaluations, total = await admin_service.get_recent_evaluations(
        limit=limit, offset=offset
    )
    return templates.TemplateResponse(
        request,
        "admin/_recent_evaluations_rows.html",
        {
            "llm_recent_evaluations": llm_recent_evaluations,
            "llm_recent_evaluations_offset": offset,
            "llm_recent_evaluations_limit": limit,
            "llm_recent_evaluations_total": total,
        },
    )


@router.get("/collection-runs/{run_id}/issues", response_class=HTMLResponse)
async def collection_run_issues(
    run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Return an HTML fragment listing the issues collected in a given run."""
    templates: Jinja2Templates = request.app.state.templates
    admin_service = AdminService(session)
    issues, total = await admin_service.get_issues_for_run(
        run_id, filtered_issues=get_config(request).filtered_issues
    )
    return templates.TemplateResponse(
        request,
        "admin/collection_run_issues.html",
        {"issues": issues, "total": total, "limit": 100},
    )


class RefreshRequest(BaseModel):
    """Parameters for an admin data refresh."""

    project: str = ""
    force_schedule: bool = False
    mode: str = "open"  # "open" | "full" | "all" | "rotation"


@router.post("/refresh")
async def trigger_refresh(
    request: Request,
    body: RefreshRequest | None = None,
    authorization: str = Header(default=""),
    _session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Trigger a data refresh.

    Requires admin authentication via Bearer token.
    Body fields (all optional):
      project: only collect this project
      force_schedule: ignore the refresh schedule and collect all/selected projects now
    """
    _require_admin_auth(request, authorization)
    _verify_origin(request)

    params = body or RefreshRequest()
    logger.info(
        "Admin: refresh queued (project=%r, mode=%s, force_schedule=%s)",
        params.project or "all",
        params.mode,
        params.force_schedule,
    )

    cmd = [
        sys.executable,
        str(
            pathlib.Path(__file__).resolve().parent.parent.parent  # noqa: ASYNC240
            / "scripts"
            / "collect_data.py"
        ),
    ]
    if params.project:
        cmd += ["--project", params.project]
    if params.force_schedule:
        cmd += ["--force-schedule"]
    if params.mode:
        cmd += ["--mode", params.mode]

    asyncio.create_task(
        asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    )

    msg = "Data refresh has been queued"
    if params.project:
        msg += f" for {params.project}"
    mode_labels = {
        "open": "open issues only",
        "full": "scheduled full refresh",
        "all": "all issues (forced)",
        "rotation": "next project in the hourly rotation",
    }
    msg += f" ({mode_labels.get(params.mode, params.mode)})"
    if params.force_schedule:
        msg += ", ignoring schedule"
    msg += "."

    return JSONResponse(
        {"status": "refresh_queued", "message": msg},
        status_code=202,
        headers=_build_toast_headers(msg, "success"),
    )


@router.get("/health")
async def admin_health(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Detailed health check: DB connectivity and collection status."""
    from sqlalchemy import text

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
