"""Shared fixtures for integration tests.

Patches SQLite's type compiler to handle PostgreSQL JSONB columns as TEXT,
so in-memory SQLite databases can be used with models that declare JSONB columns.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from craft_dashboard.dependencies import set_session_factory
from craft_dashboard.models.base import Base
from fastapi import FastAPI
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ---------------------------------------------------------------------------
# SQLite JSONB compatibility patch
# ---------------------------------------------------------------------------
# The Issue and Release models use JSONB (a PostgreSQL-specific type).
# SQLite doesn't know JSONB, so we tell its DDL compiler to use TEXT instead.
# SQLAlchemy's JSON TypeEngine still handles Python-level serialisation.
if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# No-op lifespan (bypasses PostgreSQL engine init and config-file loading)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Skip real startup so tests don't need a live Postgres or config file."""
    yield


# ---------------------------------------------------------------------------
# DB engine / session fixtures (override the parent conftest versions so that
# the JSONB patch above is guaranteed to be applied first)
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """In-memory SQLite engine with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def test_db_session(test_db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """AsyncSession backed by the in-memory SQLite engine."""
    factory = async_sessionmaker(
        test_db_engine, class_=AsyncSession, expire_on_commit=False
    )
    set_session_factory(factory)
    async with factory() as session:
        yield session
