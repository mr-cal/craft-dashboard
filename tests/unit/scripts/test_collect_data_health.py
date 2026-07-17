"""Tests for collection run health tracking in the data collection script."""

import importlib.util
import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "collect_data.py"
)
SPEC = importlib.util.spec_from_file_location("collect_data_health_script", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
collect_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect_data)


class TestCollectionStatsErrors:
    def test_merge_combines_project_errors(self) -> None:
        left = collect_data.CollectionStats(
            projects_processed={"snapcraft"},
            issues_collected=2,
            errors=[{"project": "snapcraft", "error": "boom"}],
        )
        right = collect_data.CollectionStats(
            projects_processed={"rockcraft"},
            issues_collected=3,
            errors=[{"project": "rockcraft", "error": "oops"}],
        )

        left.merge(right)

        assert left.projects_processed == {"snapcraft", "rockcraft"}
        assert left.issues_collected == 5
        assert left.errors == [
            {"project": "snapcraft", "error": "boom"},
            {"project": "rockcraft", "error": "oops"},
        ]


class TestCollectionRunTracking:
    @pytest.mark.asyncio
    async def test_main_records_completed_collection_run(self, monkeypatch) -> None:
        fake_engine = MagicMock()
        fake_engine.dispose = AsyncMock()
        settings = SimpleNamespace(
            log_level="INFO",
            config_file="craft-dashboard.toml",
            database_url="postgresql://db/test",
        )
        run = SimpleNamespace(id=7)
        create_run = AsyncMock(return_value=run)
        finish_run = AsyncMock()

        monkeypatch.setattr(collect_data, "Settings", lambda: settings)
        monkeypatch.setattr(
            collect_data,
            "load_config",
            lambda path: SimpleNamespace(maintainers=[], bots=[]),
        )
        monkeypatch.setattr(collect_data, "get_engine", lambda url: fake_engine)
        monkeypatch.setattr(
            collect_data, "get_session_factory", lambda engine: "session-factory"
        )
        monkeypatch.setattr(
            collect_data,
            "_get_running_collection_run",
            AsyncMock(return_value=None),
            raising=False,
        )
        monkeypatch.setattr(
            collect_data, "_create_collection_run", create_run, raising=False
        )
        monkeypatch.setattr(
            collect_data, "_finish_collection_run", finish_run, raising=False
        )
        monkeypatch.setattr(
            collect_data,
            "_collect_github",
            AsyncMock(
                return_value=collect_data.CollectionStats(
                    projects_processed={"snapcraft"},
                    issues_collected=4,
                    errors=[{"project": "snapcraft", "error": "temporary"}],
                )
            ),
        )
        monkeypatch.setattr(
            collect_data, "generate_cross_project_snapshot", AsyncMock()
        )

        await collect_data._main("github", 0, [], verbose=False, full_refresh=False)

        create_run.assert_awaited_once_with("github", "session-factory")
        finish_run.assert_awaited_once()
        assert finish_run.await_args.kwargs["status"] == "completed"
        assert finish_run.await_args.kwargs["projects_processed"] == 1
        assert finish_run.await_args.kwargs["issues_collected"] == 4
        assert finish_run.await_args.kwargs["errors"] == [
            {"project": "snapcraft", "error": "temporary"}
        ]

    @pytest.mark.asyncio
    async def test_main_records_failed_collection_run(self, monkeypatch) -> None:
        fake_engine = MagicMock()
        fake_engine.dispose = AsyncMock()
        settings = SimpleNamespace(
            log_level="INFO",
            config_file="craft-dashboard.toml",
            database_url="postgresql://db/test",
        )
        run = SimpleNamespace(id=8)
        create_run = AsyncMock(return_value=run)
        finish_run = AsyncMock()

        monkeypatch.setattr(collect_data, "Settings", lambda: settings)
        monkeypatch.setattr(collect_data, "load_config", lambda path: SimpleNamespace())
        monkeypatch.setattr(collect_data, "get_engine", lambda url: fake_engine)
        monkeypatch.setattr(
            collect_data, "get_session_factory", lambda engine: "session-factory"
        )
        monkeypatch.setattr(
            collect_data,
            "_get_running_collection_run",
            AsyncMock(return_value=None),
            raising=False,
        )
        monkeypatch.setattr(
            collect_data, "_create_collection_run", create_run, raising=False
        )
        monkeypatch.setattr(
            collect_data, "_finish_collection_run", finish_run, raising=False
        )
        monkeypatch.setattr(
            collect_data,
            "_collect_github",
            AsyncMock(side_effect=RuntimeError("github exploded")),
        )

        with pytest.raises(RuntimeError, match="github exploded"):
            await collect_data._main("github", 0, [], verbose=False, full_refresh=False)

        finish_run.assert_awaited_once()
        assert finish_run.await_args.kwargs["status"] == "failed"
        assert finish_run.await_args.kwargs["errors"] == [
            {"source": "github", "error": "github exploded"}
        ]


class TestConcurrencyGuard:
    @pytest.mark.asyncio
    async def test_main_exits_when_recent_collection_run_exists_for_same_source(
        self, monkeypatch
    ) -> None:
        fake_engine = MagicMock()
        fake_engine.dispose = AsyncMock()
        settings = SimpleNamespace(
            log_level="INFO",
            config_file="craft-dashboard.toml",
            database_url="postgresql://db/test",
        )
        create_run = AsyncMock()
        running_run = SimpleNamespace(
            id=5,
            started_at=datetime(2026, 7, 17, 20, 0, tzinfo=UTC),
        )

        monkeypatch.setattr(collect_data, "Settings", lambda: settings)
        monkeypatch.setattr(
            collect_data,
            "load_config",
            lambda path: SimpleNamespace(maintainers=[], bots=[]),
        )
        monkeypatch.setattr(collect_data, "get_engine", lambda url: fake_engine)
        monkeypatch.setattr(
            collect_data, "get_session_factory", lambda engine: "session-factory"
        )
        get_running = AsyncMock(return_value=running_run)
        monkeypatch.setattr(
            collect_data, "_get_running_collection_run", get_running, raising=False
        )
        monkeypatch.setattr(
            collect_data, "_create_collection_run", create_run, raising=False
        )

        with pytest.raises(SystemExit) as excinfo:
            await collect_data._main(
                "github",
                0,
                [],
                verbose=False,
                full_refresh=False,
            )

        assert excinfo.value.code != 0
        create_run.assert_not_called()
        fake_engine.dispose.assert_awaited_once()
        get_running.assert_awaited_once_with("session-factory", "github")

    @pytest.mark.asyncio
    async def test_main_proceeds_when_no_collection_run_exists(
        self, monkeypatch
    ) -> None:
        fake_engine = MagicMock()
        fake_engine.dispose = AsyncMock()
        settings = SimpleNamespace(
            log_level="INFO",
            config_file="craft-dashboard.toml",
            database_url="postgresql://db/test",
        )
        run = SimpleNamespace(id=7)
        create_run = AsyncMock(return_value=run)
        finish_run = AsyncMock()

        monkeypatch.setattr(collect_data, "Settings", lambda: settings)
        monkeypatch.setattr(
            collect_data,
            "load_config",
            lambda path: SimpleNamespace(maintainers=[], bots=[]),
        )
        monkeypatch.setattr(collect_data, "get_engine", lambda url: fake_engine)
        monkeypatch.setattr(
            collect_data, "get_session_factory", lambda engine: "session-factory"
        )
        get_running = AsyncMock(return_value=None)
        monkeypatch.setattr(
            collect_data, "_get_running_collection_run", get_running, raising=False
        )
        monkeypatch.setattr(
            collect_data, "_create_collection_run", create_run, raising=False
        )
        monkeypatch.setattr(
            collect_data, "_finish_collection_run", finish_run, raising=False
        )
        monkeypatch.setattr(
            collect_data,
            "_collect_github",
            AsyncMock(
                return_value=collect_data.CollectionStats(
                    projects_processed={"snapcraft"},
                    issues_collected=4,
                    errors=[],
                )
            ),
        )
        monkeypatch.setattr(
            collect_data, "generate_cross_project_snapshot", AsyncMock()
        )

        await collect_data._main("github", 0, [], verbose=False, full_refresh=False)

        create_run.assert_awaited_once_with("github", "session-factory")
        get_running.assert_awaited_once_with("session-factory", "github")

    @pytest.mark.asyncio
    async def test_main_ignores_running_collection_for_other_source(
        self, monkeypatch
    ) -> None:
        fake_engine = MagicMock()
        fake_engine.dispose = AsyncMock()
        settings = SimpleNamespace(
            log_level="INFO",
            config_file="craft-dashboard.toml",
            database_url="postgresql://db/test",
        )
        run = SimpleNamespace(id=7)
        create_run = AsyncMock(return_value=run)
        finish_run = AsyncMock()

        async def fake_get_running(
            _session_factory: object, source: str
        ) -> object | None:
            assert source == "github"
            return None

        monkeypatch.setattr(collect_data, "Settings", lambda: settings)
        monkeypatch.setattr(
            collect_data,
            "load_config",
            lambda path: SimpleNamespace(maintainers=[], bots=[]),
        )
        monkeypatch.setattr(collect_data, "get_engine", lambda url: fake_engine)
        monkeypatch.setattr(
            collect_data, "get_session_factory", lambda engine: "session-factory"
        )
        monkeypatch.setattr(
            collect_data,
            "_get_running_collection_run",
            AsyncMock(side_effect=fake_get_running),
            raising=False,
        )
        monkeypatch.setattr(
            collect_data, "_create_collection_run", create_run, raising=False
        )
        monkeypatch.setattr(
            collect_data, "_finish_collection_run", finish_run, raising=False
        )
        monkeypatch.setattr(
            collect_data,
            "_collect_github",
            AsyncMock(
                return_value=collect_data.CollectionStats(
                    projects_processed={"snapcraft"},
                    issues_collected=4,
                    errors=[],
                )
            ),
        )
        monkeypatch.setattr(
            collect_data, "generate_cross_project_snapshot", AsyncMock()
        )

        await collect_data._main("github", 0, [], verbose=False, full_refresh=False)

        create_run.assert_awaited_once_with("github", "session-factory")

    @pytest.mark.asyncio
    async def test_main_checks_both_sources_for_all_invocation(
        self, monkeypatch
    ) -> None:
        fake_engine = MagicMock()
        fake_engine.dispose = AsyncMock()
        settings = SimpleNamespace(
            log_level="INFO",
            config_file="craft-dashboard.toml",
            database_url="postgresql://db/test",
        )
        create_run = AsyncMock()
        launchpad_run = SimpleNamespace(
            id=6,
            started_at=datetime(2026, 7, 17, 20, 5, tzinfo=UTC),
        )

        async def fake_get_running(
            _session_factory: object, source: str
        ) -> object | None:
            if source == "github":
                return None
            if source == "launchpad":
                return launchpad_run
            return None

        get_running = AsyncMock(side_effect=fake_get_running)

        monkeypatch.setattr(collect_data, "Settings", lambda: settings)
        monkeypatch.setattr(
            collect_data,
            "load_config",
            lambda path: SimpleNamespace(maintainers=[], bots=[]),
        )
        monkeypatch.setattr(collect_data, "get_engine", lambda url: fake_engine)
        monkeypatch.setattr(
            collect_data, "get_session_factory", lambda engine: "session-factory"
        )
        monkeypatch.setattr(
            collect_data, "_get_running_collection_run", get_running, raising=False
        )
        monkeypatch.setattr(
            collect_data, "_create_collection_run", create_run, raising=False
        )

        with pytest.raises(SystemExit) as excinfo:
            await collect_data._main("all", 0, [], verbose=False, full_refresh=False)

        assert excinfo.value.code != 0
        create_run.assert_not_called()
        assert get_running.await_args_list == [
            (("session-factory", "github"), {}),
            (("session-factory", "launchpad"), {}),
        ]


class _FakeScalarResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def first(self) -> object | None:
        return self._value


class _FakeExecuteResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._value)


class _FakeRunningSession:
    def __init__(self, runs: list[SimpleNamespace]) -> None:
        self._runs = runs

    async def execute(self, stmt: object) -> _FakeExecuteResult:
        compiled = stmt.compile()
        source = next(
            value for key, value in compiled.params.items() if key.startswith("source_")
        )
        cutoff = next(
            value
            for key, value in compiled.params.items()
            if key.startswith("started_at_")
        )
        active_run = next(
            (
                run
                for run in self._runs
                if run.source == source
                and run.status == "running"
                and run.started_at >= cutoff
            ),
            None,
        )
        return _FakeExecuteResult(active_run)


def _make_running_session_factory(
    runs: list[SimpleNamespace],
) -> object:
    @asynccontextmanager
    async def _session() -> AsyncIterator[_FakeRunningSession]:
        yield _FakeRunningSession(runs)

    return _session


class TestGetRunningCollectionRun:
    @pytest.mark.asyncio
    async def test_returns_recent_running_collection_run_for_same_source(self) -> None:
        recent_run = SimpleNamespace(
            id=5,
            source="github",
            status="running",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
        )

        result = await collect_data._get_running_collection_run(
            _make_running_session_factory([recent_run]),
            "github",
        )

        assert result is recent_run

    @pytest.mark.asyncio
    async def test_ignores_running_collection_run_for_other_source(self) -> None:
        launchpad_run = SimpleNamespace(
            id=6,
            source="launchpad",
            status="running",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
        )

        result = await collect_data._get_running_collection_run(
            _make_running_session_factory([launchpad_run]),
            "github",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_stale_running_collection_run(self) -> None:
        stale_run = SimpleNamespace(
            id=7,
            source="github",
            status="running",
            started_at=datetime.now(UTC) - timedelta(hours=7),
        )

        result = await collect_data._get_running_collection_run(
            _make_running_session_factory([stale_run]),
            "github",
        )

        assert result is None
