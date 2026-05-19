"""FastAPI dependency injection helpers."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# This will be set during app startup
_session_factory: async_sessionmaker[AsyncSession] | None = None


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """Set the global session factory (called during app startup).

    Args:
        factory: The async session factory to use.

    """
    global _session_factory  # noqa: PLW0603
    _session_factory = factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for use in FastAPI route handlers.

    Yields:
        An AsyncSession that is automatically closed after the request.

    Raises:
        RuntimeError: If the session factory has not been initialized.

    """
    if _session_factory is None:
        msg = (
            "Database session factory not initialized. Call set_session_factory first."
        )
        raise RuntimeError(msg)

    async with _session_factory() as session:
        yield session
