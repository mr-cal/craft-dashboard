"""FastAPI dependency injection helpers."""

import contextvars
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_session_factory_var: contextvars.ContextVar[
    async_sessionmaker[AsyncSession] | None
] = contextvars.ContextVar("_session_factory", default=None)


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """Set the session factory (called during app startup).

    Args:
        factory: The async session factory to use.

    """
    _session_factory_var.set(factory)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for use in FastAPI route handlers.

    Yields:
        An AsyncSession that is automatically closed after the request.

    Raises:
        RuntimeError: If the session factory has not been initialized.

    """
    factory = _session_factory_var.get()
    if factory is None:
        msg = (
            "Database session factory not initialized. Call set_session_factory first."
        )
        raise RuntimeError(msg)

    async with factory() as session:
        yield session
