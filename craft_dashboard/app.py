"""FastAPI application factory for craft-dashboard."""

import json
import logging
import os
import pathlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from urllib.parse import quote as _url_quote

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from pydantic import BaseModel
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.dependencies import get_db_session, set_session_factory
from craft_dashboard.models.collection_run import CollectionRun
from craft_dashboard.routes.admin import router as admin_router
from craft_dashboard.routes.dashboard import router as dashboard_router
from craft_dashboard.routes.engagement import router as engagement_router
from craft_dashboard.routes.eval_api import limiter as eval_api_limiter
from craft_dashboard.routes.eval_api import router as eval_api_router
from craft_dashboard.routes.issues import router as issues_router
from craft_dashboard.routes.stats import router as stats_router
from craft_dashboard.settings import Settings

logger = logging.getLogger(__name__)


def _slowapi_rate_limit_handler(request: Request, exc: Exception) -> Response:
    """Adapt slowapi's handler to Starlette's broader exception type."""
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)


_PACKAGE_DIR = pathlib.Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


class JSONFormatter(logging.Formatter):
    """JSON log formatter for production structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string."""
        log_data = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown."""
    settings = getattr(app.state, "settings", Settings())
    engine = get_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    session_factory = get_session_factory(engine)
    set_session_factory(session_factory)
    app.state.settings = settings
    app.state.config = getattr(
        app.state, "config", load_config(pathlib.Path(settings.config_file))
    )
    for warning in settings.validate_required_secrets():
        logger.warning("⚠️  %s", warning)

    # Mark any runs that were "running" when the process last died as interrupted.
    # This happens when the container is restarted mid-collection.
    try:
        async with session_factory() as session:
            stuck = await session.scalars(
                select(CollectionRun).where(CollectionRun.status == "running")
            )
            stuck_runs = list(stuck)
            if stuck_runs:
                now = datetime.now(UTC)
                for run in stuck_runs:
                    run.status = "interrupted"
                    run.finished_at = now
                    if run.started_at:
                        run.duration_seconds = (now - run.started_at).total_seconds()
                    run.errors = (run.errors or []) + [
                        {
                            "source": run.source,
                            "error": "Process was killed (container restart or OOM)",
                        }
                    ]
                await session.commit()
                logger.warning(
                    "Marked %d interrupted collection run(s) as 'interrupted': ids=%s",
                    len(stuck_runs),
                    [r.id for r in stuck_runs],
                )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not check for interrupted collection runs on startup", exc_info=True
        )

    logger.info("craft-dashboard started")
    yield
    logger.info("craft-dashboard shutting down, disposing database connections...")
    await engine.dispose()
    logger.info("craft-dashboard shutdown complete")


_DURATION_MINUTE = 60
_DURATION_HOUR = 3600


def _format_duration_seconds(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string (e.g. '2m 15s')."""
    total = int(seconds)
    if total < _DURATION_MINUTE:
        return f"{total}s"
    minutes, secs = divmod(total, _DURATION_MINUTE)
    if total < _DURATION_HOUR:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, _DURATION_MINUTE)
    return f"{hours}h {mins}m {secs}s"


def _local_datetime(value: datetime | str | None, empty: str = "—") -> str:
    """Render a timestamp as a ``<time>`` element upgraded to local time by JS.

    Emits a UTC-labelled fallback (for JS-disabled clients/crawlers) using the
    same "YYYY-MM-DD HH:MM AM/PM" shape that ``upgradeLocalTimes()`` in
    base.html produces client-side, so the displayed format is identical
    whether or not JavaScript has run yet. Accepts an ISO 8601 string as well
    as a ``datetime``, since some callers pass already-serialized timestamps.
    """
    if value is None or value == "":
        return escape(empty)
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    iso = value.strftime("%Y-%m-%dT%H:%M:%SZ")
    fallback = value.strftime("%Y-%m-%d %I:%M %p UTC")
    return Markup(
        f'<time class="local-time" datetime="{iso}">{escape(fallback)}</time>'
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.

    """
    settings = Settings()

    # Configure structured logging for production (skip if handlers already
    # configured, e.g. by pytest's caplog)
    if not settings.debug and not logging.root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logging.root.handlers = [handler]
        logging.root.setLevel(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        )

    app = FastAPI(
        title="craft-dashboard",
        description="Dashboard, insights, and issue triage for *craft applications.",
        lifespan=lifespan,
        debug=settings.debug,
    )

    # Rate limiter
    limiter = eval_api_limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _slowapi_rate_limit_handler)

    app.state.settings = settings
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    # Cache-bust static assets using app startup timestamp
    _startup_ts = str(int(datetime.now(tz=UTC).timestamp()))
    template_globals = cast(dict[str, object], templates.env.globals)
    template_globals["cache_bust"] = _startup_ts
    templates.env.filters["urlencode_path"] = lambda s: _url_quote(str(s), safe="")
    templates.env.filters["format_duration"] = _format_duration_seconds
    templates.env.filters["local_datetime"] = _local_datetime
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    class HealthResponse(BaseModel):
        """Response model for health check."""

        status: str
        database: str = "ok"

    @app.get("/health", response_model=HealthResponse)
    async def health(
        session: AsyncSession = Depends(get_db_session),
    ) -> HealthResponse | JSONResponse:
        """Health check endpoint with database connectivity verification."""
        try:
            await session.execute(text("SELECT 1"))
            return HealthResponse(status="ok", database="ok")
        except Exception:  # noqa: BLE001 - health checks should degrade gracefully
            return JSONResponse(
                {"status": "degraded", "database": "error"},
                status_code=503,
            )

    @app.exception_handler(404)
    async def not_found_handler(request: Request, _exc: Exception) -> HTMLResponse:
        """Render a friendly 404 page."""
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(request, "errors/404.html", status_code=404)

    @app.exception_handler(500)
    async def server_error_handler(request: Request, _exc: Exception) -> HTMLResponse:
        """Render a friendly 500 page."""
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(request, "errors/500.html", status_code=500)

    app.include_router(dashboard_router)
    app.include_router(issues_router)
    app.include_router(stats_router)
    app.include_router(engagement_router)
    app.include_router(admin_router)
    app.include_router(eval_api_router)

    # E2E test seeding endpoint - only available when CRAFT_DASHBOARD_E2E=1
    if os.environ.get("CRAFT_DASHBOARD_E2E") == "1":

        @app.post("/e2e/seed", response_class=PlainTextResponse)
        async def e2e_seed(
            request: Request,
            session: AsyncSession = Depends(get_db_session),
        ) -> PlainTextResponse:
            """Execute SQL statements to seed the database with test data.

            Accepts a POST body containing newline-separated SQL statements.
            """
            body = (await request.body()).decode()
            for statement in body.split("\n"):
                stmt_line = statement.strip()
                if stmt_line:
                    await session.execute(text(stmt_line))
            await session.commit()
            return PlainTextResponse("OK")

    return app
