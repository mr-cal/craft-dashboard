"""Tests for collection run health tracking in the data collection script."""

import importlib.util
import pathlib
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
