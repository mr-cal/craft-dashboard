"""Shared test fixtures for craft-dashboard."""

import pathlib

import craft_dashboard
import pytest
from craft_dashboard._version import __version__
from craft_dashboard.dependencies import set_session_factory
from craft_dashboard.models.base import Base
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Path to test fixtures directory."""
    return pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def project_main_module():
    """Return the package module used by packaging/version integration tests."""
    craft_dashboard.__version__ = __version__
    return craft_dashboard


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
