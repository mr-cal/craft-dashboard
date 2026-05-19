"""Admin routes for triggering refreshes and re-evaluations."""

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.auth import verify_admin_token
from craft_dashboard.dependencies import get_db_session

router = APIRouter(prefix="/admin")


def _get_admin_token(request: Request) -> str:
    """Get the admin token from app settings."""
    return request.app.state.settings.admin_token


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
    except Exception as exc:
        db_status = f"error: {exc}"

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
