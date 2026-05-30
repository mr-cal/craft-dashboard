"""Tests for the data collection script logging."""

import importlib.util
import logging
import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql as pg_dialect

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "collect_data.py"
)
SPEC = importlib.util.spec_from_file_location("collect_data_script", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
collect_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect_data)


class _FakeResult:
    def __init__(self, value: int | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> int | None:
        return self._value

    def scalar_one(self) -> int:
        assert self._value is not None
        return self._value


class _FakeSession:
    def __init__(self, result_value: int | None) -> None:
        self._result_value = result_value

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self._result_value)


class _RecordingSession:
    """Fake session that records executed statements."""

    def __init__(self, project_id: int = 1) -> None:
        self._project_id = project_id
        self.executed_statements: list = []

    async def execute(self, stmt: object) -> _FakeResult:
        self.executed_statements.append(stmt)
        return _FakeResult(self._project_id)

    async def commit(self) -> None:
        pass


def _make_session_factory(result_value: int | None = None):
    @asynccontextmanager
    async def _session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession(result_value)

    return _session


class TestGetOrCreateProject:
    @pytest.mark.asyncio
    async def test_returns_project_id(self) -> None:
        session = _RecordingSession(project_id=42)

        result = await collect_data._get_or_create_project(
            session, "snapcraft", "application", 0
        )

        assert result == 42

    @pytest.mark.asyncio
    async def test_uses_on_conflict_do_update(self) -> None:
        """Verify the upsert updates category and display_order on conflict."""
        session = _RecordingSession(project_id=7)

        await collect_data._get_or_create_project(
            session, "snapcraft", "application", 0
        )

        upsert_stmt = session.executed_statements[0]
        compiled = str(upsert_stmt.compile(dialect=pg_dialect.dialect()))
        assert "ON CONFLICT" in compiled
        assert "DO UPDATE SET" in compiled
        assert "category" in compiled
        assert "display_order" in compiled


class TestCollectGithubLogging:
    @pytest.mark.asyncio
    async def test_collect_github_logs_timed_phase_completion(
        self, monkeypatch, caplog
    ) -> None:
        token = ("tok", "en")[0] + ("tok", "en")[1]
        settings = SimpleNamespace(github_token=token, refresh_age_days=7)
        config = SimpleNamespace(
            craft_projects=["snapcraft"],
            craft_applications=["snapcraft"],
            craft_libraries=[],
            maintainers=["alice"],
            hotfix_min_versions={},
            refresh_interval_days=7,
        )
        dep_collector = MagicMock()
        dep_collector.collect_dependencies = AsyncMock(return_value=7)
        gh_collector = MagicMock()
        gh_collector.collect_issues = AsyncMock(return_value=4)
        gh_collector.collect_releases = AsyncMock(return_value=3)

        monkeypatch.setattr(
            collect_data,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            collect_data,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            collect_data, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(collect_data, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(collect_data, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(collect_data, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            collect_data, "is_due_for_refresh", lambda next_refresh: True
        )
        monkeypatch.setattr(collect_data.asyncio, "sleep", AsyncMock())

        caplog.set_level(logging.INFO)

        result = await collect_data._collect_github(
            settings,
            config,
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
        )

        assert result.projects_processed == {"snapcraft"}
        assert result.issues_collected == 4
        assert "Collecting GitHub data for snapcraft (elapsed:" in caplog.text
        assert (
            "canonical/snapcraft: dependencies collected (7 dependencies) in "
            in caplog.text
        )
        assert "canonical/snapcraft: issues collection completed in " in caplog.text
        assert "(4 issues collected)" in caplog.text
        assert "canonical/snapcraft: releases collected (3 branches) in " in caplog.text
        assert "Generated snapshot for snapcraft in " in caplog.text


class TestReleaseCollectionIndependentOfRefresh:
    """Regression: releases must be collected even when refresh is not due."""

    @pytest.mark.asyncio
    async def test_releases_collected_when_refresh_not_due(
        self, monkeypatch, caplog
    ) -> None:
        token = ("tok", "en")[0] + ("tok", "en")[1]
        settings = SimpleNamespace(github_token=token, refresh_age_days=7)
        config = SimpleNamespace(
            craft_projects=["snapcraft"],
            craft_applications=["snapcraft"],
            craft_libraries=[],
            maintainers=["alice"],
            hotfix_min_versions={},
            refresh_interval_days=7,
        )
        dep_collector = MagicMock()
        dep_collector.collect_dependencies = AsyncMock(return_value=0)
        gh_collector = MagicMock()
        gh_collector.collect_issues = AsyncMock(return_value=0)
        gh_collector.collect_releases = AsyncMock(return_value=5)

        monkeypatch.setattr(
            collect_data,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            collect_data,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            collect_data, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(collect_data, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(collect_data, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(collect_data, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            collect_data, "is_due_for_refresh", lambda next_refresh: False
        )
        monkeypatch.setattr(collect_data.asyncio, "sleep", AsyncMock())

        caplog.set_level(logging.INFO)

        await collect_data._collect_github(
            settings,
            config,
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
        )

        gh_collector.collect_releases.assert_called_once()
        assert "releases collected (5 branches)" in caplog.text
        gh_collector.collect_issues.assert_not_called()

    @pytest.mark.asyncio
    async def test_releases_not_collected_for_non_application(
        self, monkeypatch, caplog
    ) -> None:
        """Libraries should NOT have releases collected."""
        token = ("tok", "en")[0] + ("tok", "en")[1]
        settings = SimpleNamespace(github_token=token, refresh_age_days=7)
        config = SimpleNamespace(
            craft_projects=["craft-parts"],
            craft_applications=[],  # craft-parts is a library, not app
            craft_libraries=["craft-parts"],
            maintainers=["alice"],
            hotfix_min_versions={},
            refresh_interval_days=7,
        )
        dep_collector = MagicMock()
        dep_collector.collect_dependencies = AsyncMock(return_value=0)
        gh_collector = MagicMock()
        gh_collector.collect_issues = AsyncMock(return_value=0)
        gh_collector.collect_releases = AsyncMock(return_value=0)

        monkeypatch.setattr(
            collect_data,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            collect_data,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            collect_data, "_get_or_create_project", AsyncMock(return_value=201)
        )
        monkeypatch.setattr(collect_data, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(collect_data, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(collect_data, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            collect_data, "is_due_for_refresh", lambda next_refresh: False
        )
        monkeypatch.setattr(collect_data.asyncio, "sleep", AsyncMock())

        caplog.set_level(logging.INFO)

        await collect_data._collect_github(
            settings,
            config,
            _make_session_factory(),
            projects=["craft-parts"],
            run_started_at=collect_data.time.monotonic(),
        )

        gh_collector.collect_releases.assert_not_called()


class TestCollectLaunchpadLogging:
    @pytest.mark.asyncio
    async def test_collect_launchpad_logs_bug_collection_timing(
        self, monkeypatch, caplog
    ) -> None:
        config = SimpleNamespace(
            launchpad_projects=["snapcraft"],
            craft_projects=[],
            maintainers=["alice"],
            launchpad_maintainers=["alice"],
        )
        lp_collector = MagicMock()
        lp_collector.collect_bugs = AsyncMock(return_value=150)

        monkeypatch.setattr(
            collect_data,
            "LaunchpadCollector",
            lambda projects, launchpad_maintainers: lp_collector,
        )
        monkeypatch.setattr(
            collect_data, "_get_or_create_project", AsyncMock(return_value=202)
        )
        monkeypatch.setattr(collect_data, "generate_snapshot", AsyncMock())

        caplog.set_level(logging.INFO)

        result = await collect_data._collect_launchpad(
            config,
            _make_session_factory(result_value=202),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
        )

        assert result.projects_processed == {"snapcraft"}
        assert result.issues_collected == 150
        assert "Collecting Launchpad data for snapcraft (elapsed:" in caplog.text
        assert "snapcraft (launchpad): 150 bugs fetched in " in caplog.text


class TestMainLogging:
    @pytest.mark.asyncio
    async def test_main_logs_final_collection_summary(
        self, monkeypatch, caplog
    ) -> None:
        fake_engine = MagicMock()
        fake_engine.dispose = AsyncMock()
        settings = SimpleNamespace(
            log_level="INFO",
            config_file="craft-dashboard.toml",
            database_url="postgresql://db/test",
        )

        monotonic_values = iter([10.0, 145.0])

        def fake_monotonic() -> float:
            return next(monotonic_values, 145.0)

        monkeypatch.setattr(collect_data, "Settings", lambda: settings)
        monkeypatch.setattr(collect_data, "load_config", lambda path: SimpleNamespace())
        monkeypatch.setattr(collect_data, "get_engine", lambda url: fake_engine)
        monkeypatch.setattr(
            collect_data, "get_session_factory", lambda engine: "session-factory"
        )
        monkeypatch.setattr(
            collect_data,
            "_create_collection_run",
            AsyncMock(return_value=SimpleNamespace(id=1)),
        )
        monkeypatch.setattr(
            collect_data,
            "_finish_collection_run",
            AsyncMock(),
        )
        monkeypatch.setattr(
            collect_data,
            "_collect_github",
            AsyncMock(
                return_value=collect_data.CollectionStats(
                    projects_processed={"snapcraft", "rockcraft"},
                    issues_collected=40,
                )
            ),
        )
        monkeypatch.setattr(
            collect_data,
            "_collect_launchpad",
            AsyncMock(
                return_value=collect_data.CollectionStats(
                    projects_processed={"snapcraft"},
                    issues_collected=10,
                )
            ),
        )
        monkeypatch.setattr(
            collect_data,
            "time",
            SimpleNamespace(monotonic=fake_monotonic),
        )

        caplog.set_level(logging.INFO)

        await collect_data._main("all", 0, [], verbose=False)

        assert (
            "Collection complete: 2 projects processed, 50 issues collected, total time: 2m 15s"
            in caplog.text
        )
