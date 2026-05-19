"""Shared test fixtures for craft-dashboard."""

import os
import pathlib

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession,  async_sessionmaker, create_async_engine

from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.dependencies import set_session_factory
from craft_dashboard.models.base import Base


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Path to test fixtures directory."""
    return pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
async def test_db_engine() -> AsyncEngine:
    """Create an in-memory SQLite database engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    await engine.dispose()


@pytest.fixture
async def test_db_session(test_db_engine: AsyncEngine) -> AsyncSession:
    """Create a test database session."""
    session_factory = async_sessionmaker(
        test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    set_session_factory(session_factory)
    
    async with session_factory() as session:
        yield session

