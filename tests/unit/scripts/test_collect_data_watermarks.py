"""Tests for collection watermarks in the data collection script."""

import importlib.util
import logging
import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import click.testing
import pytest

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "scripts" / "collect_data.py"
)
SPEC = importlib.util.spec_from_file_location(
    "collect_data_watermarks_script", MODULE_PATH
)
assert SPEC is not None
assert SPEC.loader is not None
collect_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect_data)

_TOKEN = "placeholder-token"


class _FakeResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value

    def scalar_one(self) -> object:
        assert self._value is not None
        return self._value


class _FakeSession:
    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(None)


def _make_session_factory():
    @asynccontextmanager
    async def _session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    return _session


class TestCollectGithubWatermarks:
    @pytest.mark.asyncio
    async def test_collect_github_uses_existing_watermark_for_incremental_collection(
        self, monkeypatch, caplog
    ) -> None:
        settings = SimpleNamespace(github_token=_TOKEN, refresh_age_days=7)
        config = SimpleNamespace(
            craft_projects=["snapcraft"],
            craft_applications=[],
            craft_libraries=[],
            maintainers=["alice"],
            hotfix_min_versions={},
            refresh_interval_days=7,
            bots=[],
            filtered_issues={},
        )
        watermark = datetime(2025, 1, 5, 12, 0, tzinfo=UTC)

        gh_collector = MagicMock()
        gh_collector.collect_issues = AsyncMock(return_value=4)
        dep_collector = MagicMock()
        dep_collector.collect_dependencies = AsyncMock(return_value=0)

        get_watermark = AsyncMock(return_value=watermark)
        upsert_watermark = AsyncMock()

        monkeypatch.setattr(
            collect_data,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            collect_data,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            collect_data, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            collect_data, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            collect_data,
            "_upsert_collection_watermark",
            upsert_watermark,
            raising=False,
        )
        monkeypatch.setattr(collect_data, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(collect_data, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(collect_data, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            collect_data, "is_due_for_refresh", lambda next_refresh: True
        )
        monkeypatch.setattr(collect_data.asyncio, "sleep", AsyncMock())

        caplog.set_level(logging.INFO)

        await collect_data._collect_github(
            settings,
            config,
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
            full_refresh=False,
            mode="full",
        )

        assert gh_collector.collect_issues.await_args.kwargs["since"] == watermark
        upsert_watermark.assert_awaited_once()
        assert "incremental collection" in caplog.text

    @pytest.mark.asyncio
    async def test_collect_github_full_refresh_ignores_saved_watermark(
        self, monkeypatch, caplog
    ) -> None:
        settings = SimpleNamespace(github_token=_TOKEN, refresh_age_days=7)
        config = SimpleNamespace(
            craft_projects=["snapcraft"],
            craft_applications=[],
            craft_libraries=[],
            maintainers=["alice"],
            hotfix_min_versions={},
            refresh_interval_days=7,
            bots=[],
            filtered_issues={},
        )
        watermark = datetime(2025, 1, 5, 12, 0, tzinfo=UTC)

        gh_collector = MagicMock()
        gh_collector.collect_issues = AsyncMock(return_value=2)
        dep_collector = MagicMock()
        dep_collector.collect_dependencies = AsyncMock(return_value=0)

        get_watermark = AsyncMock(return_value=watermark)

        monkeypatch.setattr(
            collect_data,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            collect_data,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            collect_data, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            collect_data, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            collect_data,
            "_upsert_collection_watermark",
            AsyncMock(),
            raising=False,
        )
        monkeypatch.setattr(collect_data, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(collect_data, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(collect_data, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            collect_data, "is_due_for_refresh", lambda next_refresh: True
        )
        monkeypatch.setattr(collect_data.asyncio, "sleep", AsyncMock())

        caplog.set_level(logging.INFO)

        await collect_data._collect_github(
            settings,
            config,
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
            full_refresh=True,
            mode="full",
        )

        assert gh_collector.collect_issues.await_args.kwargs["since"] is None
        assert "full collection" in caplog.text


class TestCollectGithubOpenIssueWatermarks:
    @pytest.mark.asyncio
    async def test_collect_github_open_pass_uses_open_issue_watermark(
        self, monkeypatch
    ) -> None:
        settings = SimpleNamespace(github_token=_TOKEN, refresh_age_days=7)
        config = SimpleNamespace(
            craft_projects=["snapcraft"],
            craft_applications=[],
            craft_libraries=[],
            maintainers=["alice"],
            hotfix_min_versions={},
            refresh_interval_days=7,
            bots=[],
            filtered_issues={},
        )
        open_watermark = datetime(2025, 1, 5, 12, 0, tzinfo=UTC)
        full_watermark = datetime(2025, 1, 4, 12, 0, tzinfo=UTC)

        gh_collector = MagicMock()
        gh_collector.collect_issues = AsyncMock(side_effect=[4, 2])
        gh_collector.collect_releases = AsyncMock(return_value=1)
        gh_collector.check_rate_limit.return_value = {
            "core_remaining": 5000,
            "core_limit": 5000,
            "core_reset": "later",
        }
        gh_collector.wait_for_rate_limit = MagicMock()

        dep_collector = MagicMock()
        dep_collector.collect_dependencies = AsyncMock(return_value=0)

        async def fake_get_watermark(_session, _project_id, source):
            if source == "github_issues_open":
                return open_watermark
            if source == "github":
                return full_watermark
            return None

        get_watermark = AsyncMock(side_effect=fake_get_watermark)
        upsert_watermark = AsyncMock()

        monkeypatch.setattr(
            collect_data,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            collect_data,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            collect_data, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            collect_data, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            collect_data,
            "_upsert_collection_watermark",
            upsert_watermark,
            raising=False,
        )
        monkeypatch.setattr(collect_data, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(collect_data, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(collect_data, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            collect_data, "is_due_for_refresh", lambda next_refresh: True
        )
        monkeypatch.setattr(collect_data.asyncio, "sleep", AsyncMock())

        await collect_data._collect_github(
            settings,
            config,
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
            full_refresh=False,
            mode="all",
        )

        open_call = next(
            call
            for call in gh_collector.collect_issues.await_args_list
            if call.kwargs["state"] == "open"
        )
        assert open_call.kwargs["since"] == open_watermark
        upsert_watermark.assert_any_await(
            ANY,
            101,
            "github_issues_open",
            ANY,
        )

    @pytest.mark.asyncio
    async def test_collect_github_open_pass_does_not_advance_watermark_on_failure(
        self, monkeypatch
    ) -> None:
        settings = SimpleNamespace(github_token=_TOKEN, refresh_age_days=7)
        config = SimpleNamespace(
            craft_projects=["snapcraft"],
            craft_applications=[],
            craft_libraries=[],
            maintainers=["alice"],
            hotfix_min_versions={},
            refresh_interval_days=7,
            bots=[],
            filtered_issues={},
        )
        open_watermark = datetime(2025, 1, 5, 12, 0, tzinfo=UTC)
        full_watermark = datetime(2025, 1, 4, 12, 0, tzinfo=UTC)

        gh_collector = MagicMock()
        gh_collector.collect_issues = AsyncMock(
            side_effect=[RuntimeError("open-pass failed"), 2]
        )
        gh_collector.collect_releases = AsyncMock(return_value=1)
        gh_collector.check_rate_limit.return_value = {
            "core_remaining": 5000,
            "core_limit": 5000,
            "core_reset": "later",
        }
        gh_collector.wait_for_rate_limit = MagicMock()

        dep_collector = MagicMock()
        dep_collector.collect_dependencies = AsyncMock(return_value=0)

        async def fake_get_watermark(_session, _project_id, source):
            if source == "github_issues_open":
                return open_watermark
            if source == "github":
                return full_watermark
            return None

        get_watermark = AsyncMock(side_effect=fake_get_watermark)
        upsert_watermark = AsyncMock()

        monkeypatch.setattr(
            collect_data,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            collect_data,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            collect_data, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            collect_data, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            collect_data,
            "_upsert_collection_watermark",
            upsert_watermark,
            raising=False,
        )
        monkeypatch.setattr(collect_data, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(collect_data, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(collect_data, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            collect_data, "is_due_for_refresh", lambda next_refresh: True
        )
        monkeypatch.setattr(collect_data.asyncio, "sleep", AsyncMock())

        await collect_data._collect_github(
            settings,
            config,
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
            full_refresh=False,
            mode="all",
        )

        assert not any(
            call.args[2] == "github_issues_open"
            for call in upsert_watermark.await_args_list
        )
        upsert_watermark.assert_any_await(ANY, 101, "github")


class TestCollectDataCli:
    def test_main_accepts_full_refresh_flag(self, monkeypatch) -> None:
        captured = {}

        def fake_run(coro) -> None:
            captured["full_refresh"] = coro.cr_frame.f_locals["full_refresh"]
            coro.close()

        monkeypatch.setattr(collect_data.asyncio, "run", fake_run)

        runner = click.testing.CliRunner()
        result = runner.invoke(
            collect_data.main,
            ["--source", "github", "--full-refresh"],
        )

        assert result.exit_code == 0
        assert captured["full_refresh"] is True
