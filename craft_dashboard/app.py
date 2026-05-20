"""FastAPI application factory for craft-dashboard."""

import pathlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.dependencies import set_session_factory
from craft_dashboard.routes.admin import router as admin_router
from craft_dashboard.routes.dashboard import router as dashboard_router
from craft_dashboard.routes.issues import router as issues_router
from craft_dashboard.routes.stats import router as stats_router
from craft_dashboard.settings import Settings

_PACKAGE_DIR = pathlib.Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown."""
    settings = Settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)
    set_session_factory(session_factory)
    app.state.settings = settings
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.

    """
    settings = Settings()
    app = FastAPI(
        title="craft-dashboard",
        description="Dashboard, insights, and issue triage for *craft applications.",
        lifespan=lifespan,
        debug=settings.debug,
    )

    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc) -> HTMLResponse:
        """Render a friendly 404 page."""
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(request, "errors/404.html", status_code=404)

    @app.exception_handler(500)
    async def server_error_handler(request: Request, exc) -> HTMLResponse:
        """Render a friendly 500 page."""
        templates: Jinja2Templates = request.app.state.templates
        return templates.TemplateResponse(request, "errors/500.html", status_code=500)

    app.include_router(dashboard_router)
    app.include_router(issues_router)
    app.include_router(stats_router)
    app.include_router(admin_router)

    return app
