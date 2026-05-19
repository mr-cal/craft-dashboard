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
from craft_dashboard.settings import Settings

_PACKAGE_DIR = pathlib.Path(__file__).parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Manage application startup and shutdown."""
    settings = Settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)
    set_session_factory(session_factory)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        A configured FastAPI instance.

    """
    app = FastAPI(
        title="craft-dashboard",
        description="Dashboard, insights, and issue triage for *craft applications.",
        lifespan=lifespan,
    )

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        """Render the main dashboard page."""
        return templates.TemplateResponse(request, "dashboard/index.html")

    return app
