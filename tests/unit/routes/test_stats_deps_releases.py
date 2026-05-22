"""Unit tests for stats dependency and release routes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.dependency import Dependency
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release

if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"

_idx = next(
    (
        i
        for i in LLMEvaluation.__table__.indexes
        if i.name == "ix_llm_evaluations_latest_issue"
    ),
    None,
)
if _idx is not None:
    _idx.dialect_options.pop("postgresql", None)


@asynccontextmanager
async def _noop_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


@pytest.fixture
def test_client(test_db_session: AsyncSession) -> AsyncGenerator[TestClient, None]:
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    app.state.config = DashboardConfig(
        craft_libraries=["craft-parts", "craft-providers"]
    )

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield test_db_session

    app.dependency_overrides[get_db_session] = _override

    with TestClient(app) as client:
        yield client


def _project(name: str, *, category: str = "application") -> Project:
    return Project(
        name=name,
        category=category,
        github_org="canonical",
        display_order=10,
    )


def _dependency(
    project_id: int,
    *,
    dependency_name: str = "craft-parts",
    branch: str = "main",
    version_spec: str | None = ">=1.0",
    installed_version: str | None = "1.2.3",
    latest_version: str | None = "1.3.0",
    series: str | None = "1.x",
    is_outdated: bool | None = True,
) -> Dependency:
    return Dependency(
        project_id=project_id,
        branch=branch,
        dependency_name=dependency_name,
        version_spec=version_spec,
        source_file="requirements.txt",
        fetched_at=datetime.now(tz=UTC),
        installed_version=installed_version,
        latest_version=latest_version,
        series=series,
        is_outdated=is_outdated,
    )


def _release(
    project_id: int,
    *,
    version: str = "8.3.1",
    branch: str = "stable",
    metadata_: dict | None = None,
) -> Release:
    return Release(
        project_id=project_id,
        version=version,
        branch=branch,
        released_at=datetime(2024, 5, 1, tzinfo=UTC),
        is_hotfix=False,
        metadata_=metadata_ or {},
    )


class TestDependenciesData:
    @pytest.fixture
    async def seeded_dependency(self, test_db_session: AsyncSession) -> None:
        project = _project("snapcraft")
        test_db_session.add(project)
        await test_db_session.flush()
        test_db_session.add(_dependency(project.id))
        await test_db_session.commit()

    def test_deps_data_structure(
        self, test_client: TestClient, seeded_dependency: None
    ) -> None:
        response = test_client.get("/stats/dependencies/data")

        assert response.status_code == 200
        assert response.json() == {
            "libs": ["craft-parts", "craft-providers"],
            "apps": {
                "snapcraft/main": {
                    "craft-parts": {
                        "version": "1.2.3",
                        "latest": "1.3.0",
                        "series": "1.x",
                        "outdated": True,
                    }
                }
            },
        }

    def test_deps_excludes_non_application(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("craft-parts", category="library")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(_dependency(project.id))
            await test_db_session.commit()

        import asyncio  # noqa: PLC0415

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/dependencies/data")

        assert response.status_code == 200
        assert response.json() == {
            "libs": ["craft-parts", "craft-providers"],
            "apps": {},
        }

    def test_deps_without_installed_version(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("rockcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(
                _dependency(
                    project.id,
                    dependency_name="craft-providers",
                    installed_version=None,
                    version_spec="~=2.4",
                    latest_version=None,
                    series=None,
                    is_outdated=None,
                )
            )
            await test_db_session.commit()

        import asyncio  # noqa: PLC0415

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/dependencies/data")

        assert response.status_code == 200
        assert response.json()["apps"] == {
            "rockcraft/main": {
                "craft-providers": {
                    "version_spec": "~=2.4",
                }
            }
        }

    def test_deps_only_includes_craft_libraries(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("charmcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(_dependency(project.id, dependency_name="requests"))
            await test_db_session.commit()

        import asyncio  # noqa: PLC0415

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/dependencies/data")

        assert response.status_code == 200
        assert "charmcraft/main" not in response.json()["apps"]

    def test_deps_empty_db(self, test_client: TestClient) -> None:
        response = test_client.get("/stats/dependencies/data")

        assert response.status_code == 200
        assert response.json() == {
            "libs": ["craft-parts", "craft-providers"],
            "apps": {},
        }


class TestReleasesPage:
    def test_releases_shows_version(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("snapcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(_release(project.id, version="8.3.1"))
            await test_db_session.commit()

        import asyncio  # noqa: PLC0415

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "8.3.1" in response.text

    def test_releases_shows_commits_since(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("rockcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(
                _release(project.id, metadata_={"commits_since_tag": 15})
            )
            await test_db_session.commit()

        import asyncio  # noqa: PLC0415

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "<td>15</td>" in response.text

    def test_releases_empty_db(self, test_client: TestClient) -> None:
        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "Releases" in response.text
