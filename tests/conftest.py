"""Shared test fixtures for craft-dashboard."""

import os
import pathlib

import craft_dashboard
import pytest
from craft_dashboard._version import __version__
from craft_dashboard.dependencies import set_session_factory
from craft_dashboard.models.base import Base
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip e2e tests unless CRAFT_DASHBOARD_E2E=1 is set."""
    if os.environ.get("CRAFT_DASHBOARD_E2E") == "1":
        return
    skip_e2e = pytest.mark.skip(
        reason="set CRAFT_DASHBOARD_E2E=1 to run end-to-end tests"
    )
    for item in items:
        if item.get_closest_marker("e2e"):
            item.add_marker(skip_e2e)


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Path to test fixtures directory."""
    return pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def project_main_module():
    """Return the package module used by packaging/version integration tests."""
    craft_dashboard.__version__ = __version__
    return craft_dashboard


async def _configure_sqlite_indexes(engine: AsyncEngine) -> None:
    """Restore SQLite compatibility for partial unique indexes used in tests."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_llm_evaluations_latest_issue"))
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX ix_llm_evaluations_latest_issue "
                "ON llm_evaluations (issue_id) WHERE latest = 1"
            )
        )


@pytest.fixture
async def test_db_engine() -> AsyncEngine:
    """Create an in-memory SQLite database engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _configure_sqlite_indexes(engine)

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
