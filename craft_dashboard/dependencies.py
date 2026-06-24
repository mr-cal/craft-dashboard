"""FastAPI dependency injection helpers."""

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from craft_dashboard.config import DashboardConfig

_session_factory: async_sessionmaker[AsyncSession] | None = None


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """Set the session factory (called during app startup)."""
    global _session_factory
    _session_factory = factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for use in FastAPI route handlers."""
    if _session_factory is None:
        msg = (
            "Database session factory not initialized. Call set_session_factory first."
        )
        raise RuntimeError(msg)

    async with _session_factory() as session:
        yield session


def get_config(request: Request) -> DashboardConfig:
    """Return the app DashboardConfig, falling back to an empty default for tests."""
    return getattr(request.app.state, "config", DashboardConfig())
