"""Tests for collection watermarks in the data collection script."""

import importlib.util
import logging
import pathlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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

from scripts.collect import github_pass  # noqa: E402

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
        settings = SimpleNamespace(
            github_token=_TOKEN,
            refresh_age_days=7,
            collect_dependencies_interval_hours=24,
            collect_releases_interval_hours=24,
            collect_ci_interval_hours=1,
        )
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
            github_pass,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            github_pass,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            github_pass, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            github_pass, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            github_pass,
            "_upsert_collection_watermark",
            upsert_watermark,
            raising=False,
        )
        monkeypatch.setattr(github_pass, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(github_pass, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(github_pass, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            github_pass, "is_due_for_refresh", lambda next_refresh: True
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
        github_upserts = [
            call
            for call in upsert_watermark.await_args_list
            if call.args[2] == "github"
        ]
        assert len(github_upserts) == 1
        assert "incremental collection" in caplog.text

    @pytest.mark.asyncio
    async def test_collect_github_full_refresh_ignores_saved_watermark(
        self, monkeypatch, caplog
    ) -> None:
        settings = SimpleNamespace(
            github_token=_TOKEN,
            refresh_age_days=7,
            collect_dependencies_interval_hours=24,
            collect_releases_interval_hours=24,
            collect_ci_interval_hours=1,
        )
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
            github_pass,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            github_pass,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            github_pass, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            github_pass, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            github_pass,
            "_upsert_collection_watermark",
            AsyncMock(),
            raising=False,
        )
        monkeypatch.setattr(github_pass, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(github_pass, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(github_pass, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            github_pass, "is_due_for_refresh", lambda next_refresh: True
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
        assert gh_collector.collect_issues.await_args.kwargs["state"] == "full"
        assert "full collection" in caplog.text


class TestCollectGithubOpenIssueWatermarks:
    @pytest.mark.asyncio
    async def test_collect_github_open_pass_uses_open_issue_watermark(
        self, monkeypatch
    ) -> None:
        settings = SimpleNamespace(
            github_token=_TOKEN,
            refresh_age_days=7,
            collect_dependencies_interval_hours=24,
            collect_releases_interval_hours=24,
            collect_ci_interval_hours=1,
        )
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
            github_pass,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            github_pass,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            github_pass, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            github_pass, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            github_pass,
            "_upsert_collection_watermark",
            upsert_watermark,
            raising=False,
        )
        monkeypatch.setattr(github_pass, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(github_pass, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(github_pass, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            github_pass, "is_due_for_refresh", lambda next_refresh: True
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
        settings = SimpleNamespace(
            github_token=_TOKEN,
            refresh_age_days=7,
            collect_dependencies_interval_hours=24,
            collect_releases_interval_hours=24,
            collect_ci_interval_hours=1,
        )
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
            github_pass,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            github_pass,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            github_pass, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            github_pass, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            github_pass,
            "_upsert_collection_watermark",
            upsert_watermark,
            raising=False,
        )
        monkeypatch.setattr(github_pass, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(github_pass, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(github_pass, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            github_pass, "is_due_for_refresh", lambda next_refresh: True
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


class TestPhaseGating:
    """The open pass gates deps/releases/CI on per-phase watermark intervals."""

    @staticmethod
    def _settings() -> SimpleNamespace:
        return SimpleNamespace(
            github_token=_TOKEN,
            refresh_age_days=7,
            collect_dependencies_interval_hours=24,
            collect_releases_interval_hours=24,
            collect_ci_interval_hours=1,
        )

    @staticmethod
    def _config() -> SimpleNamespace:
        return SimpleNamespace(
            craft_projects=["snapcraft"],
            craft_applications=[],
            craft_libraries=[],
            maintainers=["alice"],
            hotfix_min_versions={},
            refresh_interval_days=7,
            bots=[],
            filtered_issues={},
        )

    @staticmethod
    def _mock_collectors():
        gh_collector = MagicMock()
        gh_collector.collect_issues = AsyncMock(return_value=3)
        gh_collector.collect_releases = AsyncMock(return_value=2)
        dep_collector = MagicMock()
        dep_collector.collect_dependencies = AsyncMock(return_value=0)
        ci_collector = MagicMock()
        ci_collector.collect = AsyncMock(return_value=0)
        return gh_collector, dep_collector, ci_collector

    @staticmethod
    def _patch(monkeypatch, gh_collector, dep_collector, ci_collector, get_watermark):
        upsert_watermark = AsyncMock()
        monkeypatch.setattr(
            github_pass,
            "GitHubCollector",
            lambda token, org, maintainers: gh_collector,
        )
        monkeypatch.setattr(
            github_pass,
            "DependencyCollector",
            lambda token, org, craft_libraries: dep_collector,
        )
        monkeypatch.setattr(
            github_pass, "CIFailureCollector", lambda token, org: ci_collector
        )
        monkeypatch.setattr(
            github_pass, "_get_or_create_project", AsyncMock(return_value=101)
        )
        monkeypatch.setattr(
            github_pass, "_get_collection_watermark", get_watermark, raising=False
        )
        monkeypatch.setattr(
            github_pass,
            "_upsert_collection_watermark",
            upsert_watermark,
            raising=False,
        )
        monkeypatch.setattr(github_pass, "generate_snapshot", AsyncMock())
        monkeypatch.setattr(github_pass, "update_refresh_schedule", AsyncMock())
        monkeypatch.setattr(github_pass, "record_refresh_error", AsyncMock())
        monkeypatch.setattr(
            github_pass, "is_due_for_refresh", lambda next_refresh: True
        )
        monkeypatch.setattr(collect_data.asyncio, "sleep", AsyncMock())
        return upsert_watermark

    @staticmethod
    def _watermark_source_by(source: str, hours_ago: float | None):
        """Return a fake watermark getter mapping phase sources to freshness."""

        async def fake_get_watermark(_session, _project_id, src):
            if hours_ago is None and src == source:
                return None
            if src == source:
                return datetime.now(UTC) - timedelta(hours=hours_ago)
            if src == "github_issues_open":
                return datetime(2025, 1, 5, 12, 0, tzinfo=UTC)
            return None

        return fake_get_watermark

    @pytest.mark.asyncio
    async def test_open_pass_skips_phases_with_fresh_watermarks(
        self, monkeypatch
    ) -> None:
        gh_collector, dep_collector, ci_collector = self._mock_collectors()
        get_watermark = AsyncMock(
            side_effect=self._watermark_source_by("github:deps", 2)
        )
        upsert_watermark = self._patch(
            monkeypatch, gh_collector, dep_collector, ci_collector, get_watermark
        )

        await collect_data._collect_github(
            self._settings(),
            self._config(),
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
            full_refresh=False,
            mode="open",
        )

        # deps: 2h < 24h interval → skipped. releases: no watermark → run.
        # ci: 2h watermark is for "github:deps" only; "github:ci" has none → run.
        dep_collector.collect_dependencies.assert_not_awaited()
        gh_collector.collect_releases.assert_awaited_once()
        ci_collector.collect.assert_awaited_once()
        gh_collector.collect_issues.assert_awaited_once()
        upserted = [call.args[2] for call in upsert_watermark.await_args_list]
        assert "github:releases" in upserted
        assert "github:ci" in upserted
        assert "github:deps" not in upserted

    @pytest.mark.asyncio
    async def test_open_pass_runs_all_phases_when_watermarks_stale(
        self, monkeypatch
    ) -> None:
        gh_collector, dep_collector, ci_collector = self._mock_collectors()

        async def fake_get_watermark(_session, _project_id, src):
            if src == "github:deps":
                return datetime.now(UTC) - timedelta(hours=25)
            if src == "github:releases":
                return datetime.now(UTC) - timedelta(hours=25)
            if src == "github:ci":
                return datetime.now(UTC) - timedelta(hours=2)
            return None

        upsert_watermark = self._patch(
            monkeypatch, gh_collector, dep_collector, ci_collector, fake_get_watermark
        )

        await collect_data._collect_github(
            self._settings(),
            self._config(),
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
            full_refresh=False,
            mode="open",
        )

        dep_collector.collect_dependencies.assert_awaited_once()
        gh_collector.collect_releases.assert_awaited_once()
        ci_collector.collect.assert_awaited_once()
        upserted = [call.args[2] for call in upsert_watermark.await_args_list]
        assert {"github:deps", "github:releases", "github:ci"} <= set(upserted)

    @pytest.mark.asyncio
    async def test_failed_phase_does_not_advance_its_watermark(
        self, monkeypatch
    ) -> None:
        gh_collector, dep_collector, ci_collector = self._mock_collectors()
        dep_collector.collect_dependencies = AsyncMock(
            side_effect=RuntimeError("deps failed")
        )
        get_watermark = AsyncMock(side_effect=self._watermark_source_by(None, None))
        upsert_watermark = self._patch(
            monkeypatch, gh_collector, dep_collector, ci_collector, get_watermark
        )

        await collect_data._collect_github(
            self._settings(),
            self._config(),
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
            full_refresh=False,
            mode="open",
        )

        upserted = [call.args[2] for call in upsert_watermark.await_args_list]
        assert "github:deps" not in upserted
        assert "github:releases" in upserted

    @pytest.mark.asyncio
    async def test_full_refresh_forces_all_phases_despite_fresh_watermarks(
        self, monkeypatch
    ) -> None:
        gh_collector, dep_collector, ci_collector = self._mock_collectors()
        get_watermark = AsyncMock(
            side_effect=self._watermark_source_by("github:deps", 2)
        )
        upsert_watermark = self._patch(
            monkeypatch, gh_collector, dep_collector, ci_collector, get_watermark
        )

        await collect_data._collect_github(
            self._settings(),
            self._config(),
            _make_session_factory(),
            projects=["snapcraft"],
            run_started_at=collect_data.time.monotonic(),
            full_refresh=True,
            mode="all",
        )

        dep_collector.collect_dependencies.assert_awaited_once()
        gh_collector.collect_releases.assert_awaited_once()
        ci_collector.collect.assert_awaited_once()
        upserted = [call.args[2] for call in upsert_watermark.await_args_list]
        assert {"github:deps", "github:releases", "github:ci"} <= set(upserted)


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
