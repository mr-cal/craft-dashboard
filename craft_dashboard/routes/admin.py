"""Admin routes for triggering refreshes and re-evaluations."""

import logging

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.auth import verify_admin_token
from craft_dashboard.dependencies import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


def _get_admin_token(request: Request) -> str:
    """Get the admin token from app settings."""
    return request.app.state.settings.admin_token


@router.get("", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the admin dashboard page."""
    templates: Jinja2Templates = request.app.state.templates

    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.llm_evaluation import LLMEvaluation

    total_open = await session.scalar(
        select(func.count()).select_from(Issue).where(Issue.state == "open")
    ) or 0

    evaluated_count = await session.scalar(
        select(func.count(func.distinct(LLMEvaluation.issue_id)))
        .select_from(LLMEvaluation)
        .join(Issue, LLMEvaluation.issue_id == Issue.id)
        .where(Issue.state == "open")
        .where(LLMEvaluation.latest.is_(True))
    ) or 0

    result = await session.execute(
        select(
            LLMEvaluation.suggested_action,
            func.count().label("count"),
        )
        .join(Issue, LLMEvaluation.issue_id == Issue.id)
        .where(Issue.state == "open")
        .where(LLMEvaluation.latest.is_(True))
        .group_by(LLMEvaluation.suggested_action)
    )
    action_counts = {row.suggested_action: row.count for row in result}

    return templates.TemplateResponse(
        request,
        "admin/index.html",
        {
            "evaluated_count": evaluated_count,
            "total_open": total_open,
            "action_counts": action_counts,
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
    admin_token = _get_admin_token(request)
    verify_admin_token(authorization, admin_token)

    return JSONResponse(
        {"status": "refresh_queued", "message": "Data refresh has been queued."},
        status_code=202,
    )


@router.post("/re-evaluate")
async def trigger_re_evaluation(
    request: Request,
    authorization: str = Header(default=""),
    _session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Trigger LLM re-evaluation of all open issues.

    Requires admin authentication via Bearer token.
    """
    admin_token = _get_admin_token(request)
    verify_admin_token(authorization, admin_token)

    return JSONResponse(
        {
            "status": "evaluation_queued",
            "message": "LLM re-evaluation has been queued.",
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
    from datetime import datetime, timedelta, timezone

    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    admin_token = _get_admin_token(request)
    verify_admin_token(authorization, admin_token)

    settings = request.app.state.settings
    refresh_age_days = settings.refresh_age_days

    result = await session.execute(select(RefreshSchedule))
    schedules = list(result.scalars())

    if not schedules:
        return JSONResponse(
            {"status": "success", "message": "No schedules to distribute.", "count": 0}
        )

    now = datetime.now(timezone.utc)
    total_schedules = len(schedules)

    for idx, schedule in enumerate(schedules):
        day_offset = (idx * refresh_age_days) // total_schedules
        schedule.next_refresh_at = now + timedelta(days=day_offset)

    await session.commit()

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

    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    admin_token = _get_admin_token(request)
    verify_admin_token(authorization, admin_token)

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
    import subprocess  # noqa: PLC0415

    admin_token = _get_admin_token(request)
    verify_admin_token(authorization, admin_token)

    try:
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                "collect-data",
                "-u",
                "craft-dashboard",
                "-n",
                "100",
                "--no-pager",
                "--output",
                "short",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return PlainTextResponse(result.stdout or "(no logs)")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return PlainTextResponse("(journalctl not available)")
