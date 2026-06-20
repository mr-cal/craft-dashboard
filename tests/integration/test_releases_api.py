"""Integration tests for the releases stats page.

Covers bug 5: Releases page shows data when release records exist in the DB.

Before the fix the page rendered "No release data" even when Release rows
were present, because the query filtered on Project.category == "application"
but the test data used a different category value.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Skip real startup so tests don't need a live Postgres or config file."""
    yield


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> TestClient:
    """TestClient wired to the in-memory SQLite session."""
    app = create_app()
    app.router.lifespan_context = _noop_lifespan

    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override_session

    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Bug 5: Releases page shows release data when records exist
# ---------------------------------------------------------------------------


class TestReleasesPage:
    """Bug 5: The /stats/releases page must display existing release records."""

    @pytest.fixture
    async def seeded(self, test_db_session: AsyncSession) -> None:
        """Create an application project with a release record."""
        p = Project(
            name="snapcraft",
            category="application",
            github_org="canonical",
        )
        test_db_session.add(p)
        await test_db_session.flush()

        test_db_session.add(
            Release(
                project_id=p.id,
                version="8.3.1",
                branch="stable",
                released_at=datetime(2024, 5, 1, tzinfo=UTC),
                is_hotfix=False,
                metadata_={},
            )
        )
        await test_db_session.commit()

    def test_release_version_appears_in_page(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """The releases page body must contain the release version string."""
        response = test_client.get("/stats/releases")
        assert response.status_code == 200
        assert "8.3.1" in response.text, (
            "Release version not found in response — page may be showing 'no data'"
        )

    def test_no_release_data_message_absent(
        self, test_client: TestClient, seeded: None
    ) -> None:
        """The 'No release data' placeholder must not appear when data exists."""
        response = test_client.get("/stats/releases")
        assert response.status_code == 200
        # The template should not fall through to a no-data message
        assert "No release data" not in response.text

    def test_releases_page_empty_db(self, test_client: TestClient) -> None:
        """The releases page renders successfully even with an empty database."""
        response = test_client.get("/stats/releases")
        assert response.status_code == 200

    def test_non_application_project_not_shown(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        """Releases for non-application projects must not appear on the page."""

        async def _seed() -> None:
            p = Project(
                name="craft-parts-lib",
                category="library",
                github_org="canonical",
            )
            test_db_session.add(p)
            await test_db_session.flush()
            test_db_session.add(
                Release(
                    project_id=p.id,
                    version="2.0.0-library",
                    branch="main",
                    released_at=datetime(2024, 4, 1, tzinfo=UTC),
                    is_hotfix=False,
                    metadata_={},
                )
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")
        assert response.status_code == 200
        assert "2.0.0-library" not in response.text

    def test_latest_release_per_project_branch_is_shown(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        """Only the newest release for each project+branch should be rendered."""

        async def _seed() -> None:
            project = Project(
                name="charmcraft",
                category="application",
                github_org="canonical",
            )
            test_db_session.add(project)
            await test_db_session.flush()
            await test_db_session.execute(text("DROP TABLE releases"))
            await test_db_session.execute(
                text(
                    """
                    CREATE TABLE releases (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id INTEGER NOT NULL,
                        version VARCHAR(100) NOT NULL,
                        branch VARCHAR(255),
                        released_at DATETIME,
                        is_hotfix BOOLEAN NOT NULL DEFAULT 0,
                        metadata TEXT
                    )
                    """
                )
            )
            await test_db_session.execute(
                text(
                    """
                    INSERT INTO releases
                        (project_id, version, branch, released_at, is_hotfix, metadata)
                    VALUES
                        (:project_id, :version, :branch, :released_at, :is_hotfix, :metadata)
                    """
                ),
                [
                    {
                        "project_id": project.id,
                        "version": "1.0.0",
                        "branch": "stable",
                        "released_at": datetime(2024, 1, 1, tzinfo=UTC),
                        "is_hotfix": False,
                        "metadata": "{}",
                    },
                    {
                        "project_id": project.id,
                        "version": "2.0.0",
                        "branch": "stable",
                        "released_at": datetime(2024, 2, 1, tzinfo=UTC),
                        "is_hotfix": False,
                        "metadata": "{}",
                    },
                ],
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")
        assert response.status_code == 200
        assert "2.0.0" in response.text
        assert "1.0.0" not in response.text

    def test_only_latest_minor_per_major_shown(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        """Only the latest minor version per major version should be shown.

        e.g. if charmcraft has hotfix/4.0, hotfix/4.1, hotfix/4.2 branches,
        only hotfix/4.2 should appear.
        """

        async def _seed() -> None:
            project = Project(
                name="charmcraft",
                category="application",
                github_org="canonical",
            )
            test_db_session.add(project)
            await test_db_session.flush()
            await test_db_session.execute(text("DROP TABLE releases"))
            await test_db_session.execute(
                text(
                    """
                    CREATE TABLE releases (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id INTEGER NOT NULL,
                        version VARCHAR(100) NOT NULL,
                        branch VARCHAR(255),
                        released_at DATETIME,
                        is_hotfix BOOLEAN NOT NULL DEFAULT 0,
                        metadata TEXT
                    )
                    """
                )
            )
            await test_db_session.execute(
                text(
                    """
                    INSERT INTO releases
                        (project_id, version, branch, released_at, is_hotfix, metadata)
                    VALUES
                        (:project_id, :version, :branch, :released_at, :is_hotfix, :metadata)
                    """
                ),
                [
                    {
                        "project_id": project.id,
                        "version": "4.0.0",
                        "branch": "hotfix/4.0",
                        "released_at": datetime(2024, 1, 1, tzinfo=UTC),
                        "is_hotfix": False,
                        "metadata": "{}",
                    },
                    {
                        "project_id": project.id,
                        "version": "4.1.0",
                        "branch": "hotfix/4.1",
                        "released_at": datetime(2024, 2, 1, tzinfo=UTC),
                        "is_hotfix": False,
                        "metadata": "{}",
                    },
                    {
                        "project_id": project.id,
                        "version": "4.2.0",
                        "branch": "hotfix/4.2",
                        "released_at": datetime(2024, 3, 1, tzinfo=UTC),
                        "is_hotfix": False,
                        "metadata": "{}",
                    },
                    {
                        "project_id": project.id,
                        "version": "3.5.0",
                        "branch": "hotfix/3.5",
                        "released_at": datetime(2024, 1, 15, tzinfo=UTC),
                        "is_hotfix": False,
                        "metadata": "{}",
                    },
                    {
                        "project_id": project.id,
                        "version": "5.0.0",
                        "branch": "main",
                        "released_at": datetime(2024, 4, 1, tzinfo=UTC),
                        "is_hotfix": False,
                        "metadata": "{}",
                    },
                ],
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")
        assert response.status_code == 200
        # hotfix/4.2 should be shown in the Releases table (latest minor of major 4)
        assert "4.2.0" in response.text
        # hotfix/4.0 and hotfix/4.1 are excluded from the Releases table filtering,
        # but they DO appear in the Hotfixes section (which shows all hotfix/* branches).
        assert response.text.count("4.0.0") >= 1  # present in Hotfixes table
        assert response.text.count("4.1.0") >= 1  # present in Hotfixes table
        # hotfix/3.5 should be shown (only release for major 3)
        assert "3.5.0" in response.text
        # main branch (non-versioned) should be shown
        assert "5.0.0" in response.text
