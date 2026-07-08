"""Database connection and session management."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def get_engine(
    database_url: str,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Args:
        database_url: PostgreSQL connection URL with asyncpg driver.
        pool_size: Number of persistent connections in the pool.
        max_overflow: Number of connections allowed beyond pool_size.

    Returns:
        An AsyncEngine instance.

    """
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        echo=False,
        # Force custom plans so PostgreSQL always plans with actual parameter
        # values. Without this, asyncpg's prepared statement cache causes
        # PostgreSQL to switch to generic plans after 5+ executions, which
        # cannot match partial index conditions like `model_name != 'pending'`
        # against a bind parameter, causing catastrophic plan regressions (e.g.
        # a full-table cross-join taking 15s instead of 0.3ms).
        connect_args={"server_settings": {"plan_cache_mode": "force_custom_plan"}},
    )


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The async SQLAlchemy engine.

    Returns:
        An async_sessionmaker that produces AsyncSession instances.

    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
