"""Unit tests for stats dependency and release routes."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from craft_dashboard.app import create_app
from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_db_session
from craft_dashboard.models.dependency import Dependency
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.models.release import Release
from craft_dashboard.routes.stats import dependencies_data
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.ext.asyncio import AsyncSession

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

    @pytest.mark.asyncio
    async def test_deps_query_filters_to_configured_libraries(self) -> None:
        class RecordingSession:
            def __init__(self) -> None:
                self.statement = None

            async def execute(self, statement):
                self.statement = statement
                return [
                    SimpleNamespace(
                        project_name="snapcraft",
                        branch="main",
                        dependency_name="craft-parts",
                        version_spec=">=1.0",
                        installed_version="1.2.3",
                        latest_version="1.3.0",
                        series="1.x",
                        is_outdated=True,
                    ),
                    SimpleNamespace(
                        project_name="snapcraft",
                        branch="main",
                        dependency_name="requests",
                        version_spec=">=2.0",
                        installed_version="2.31.0",
                        latest_version="2.32.0",
                        series="2.x",
                        is_outdated=True,
                    ),
                ]

        session = RecordingSession()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config=DashboardConfig(
                        craft_libraries=["craft-parts", "craft-providers"]
                    )
                )
            )
        )

        response = await dependencies_data(request, session)

        compiled = str(
            session.statement.compile(compile_kwargs={"literal_binds": True})
        )
        assert (
            "dependencies.dependency_name IN ('craft-parts', 'craft-providers')"
            in compiled
        )
        assert response == {
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

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "<td>15</td>" in response.text

    def test_releases_empty_db(self, test_client: TestClient) -> None:
        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "Releases" in response.text


class TestHotfixesSection:
    def test_hotfixes_section_heading_shown(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("charmcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(
                _release(project.id, branch="hotfix/4.1", version="4.1.2")
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "Hotfixes" in response.text

    def test_hotfixes_shows_all_hotfix_branches(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("charmcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(
                _release(project.id, branch="hotfix/4.0", version="4.0.1")
            )
            test_db_session.add(
                _release(project.id, branch="hotfix/4.1", version="4.1.2")
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "4.0.1" in response.text
        assert "4.1.2" in response.text

    def test_hotfixes_includes_non_application_projects(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            lib_project = _project("craft-parts", category="library")
            test_db_session.add(lib_project)
            await test_db_session.flush()
            test_db_session.add(
                _release(lib_project.id, branch="hotfix/1.5", version="1.5.3")
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "1.5.3" in response.text

    def test_hotfixes_safe_to_delete_yes(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("snapcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(
                _release(
                    project.id,
                    branch="hotfix/8.3",
                    version="8.3.1",
                    metadata_={"commits_since_tag": 0, "tag_on_main": True},
                )
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "Yes" in response.text

    def test_hotfixes_safe_to_delete_no_when_commits_exist(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("rockcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(
                _release(
                    project.id,
                    branch="hotfix/1.5",
                    version="1.5.0",
                    metadata_={"commits_since_tag": 3, "tag_on_main": True},
                )
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "No" in response.text

    def test_hotfixes_safe_to_delete_no_when_tag_not_on_main(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("charmcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(
                _release(
                    project.id,
                    branch="hotfix/3.1",
                    version="3.1.0",
                    metadata_={"commits_since_tag": 0, "tag_on_main": False},
                )
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "No" in response.text

    def test_hotfixes_safe_to_delete_unknown_when_no_metadata(
        self, test_client: TestClient, test_db_session: AsyncSession
    ) -> None:
        async def _seed() -> None:
            project = _project("snapcraft")
            test_db_session.add(project)
            await test_db_session.flush()
            test_db_session.add(
                _release(
                    project.id,
                    branch="hotfix/8.0",
                    version="8.0.0",
                    metadata_={},
                )
            )
            await test_db_session.commit()

        asyncio.get_event_loop().run_until_complete(_seed())

        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "Yes" not in response.text
        assert "No" not in response.text

    def test_hotfixes_empty_shows_placeholder(self, test_client: TestClient) -> None:
        response = test_client.get("/stats/releases")

        assert response.status_code == 200
        assert "No hotfix branches" in response.text
