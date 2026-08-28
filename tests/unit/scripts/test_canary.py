"""Tests for scripts.llm.canary — the Phase 6 canary rollout tool."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import click
import pytest
from click.testing import CliRunner
from scripts.llm.canary import CanaryTarget, cli, parse_targets


class TestParseTargets:
    """parse_targets() converts PROJECT:NUMBER strings into CanaryTargets."""

    def test_parses_single_target(self) -> None:
        targets = parse_targets(("snapcraft:6381",))

        assert targets == [CanaryTarget(project="snapcraft", issue="6381")]

    def test_parses_multiple_targets_in_order(self) -> None:
        targets = parse_targets(("snapcraft:6381", "debcraft:41"))

        assert targets == [
            CanaryTarget(project="snapcraft", issue="6381"),
            CanaryTarget(project="debcraft", issue="41"),
        ]

    def test_rejects_missing_separator(self) -> None:
        with pytest.raises(click.UsageError, match="PROJECT:NUMBER"):
            parse_targets(("snapcraft6381",))

    def test_rejects_empty_project(self) -> None:
        with pytest.raises(click.UsageError, match="PROJECT:NUMBER"):
            parse_targets((":6381",))

    def test_rejects_empty_issue(self) -> None:
        with pytest.raises(click.UsageError, match="PROJECT:NUMBER"):
            parse_targets(("snapcraft:",))


class TestCanaryCli:
    """The canary CLI evaluates each target one at a time and stops on failure."""

    def _base_args(self) -> list[str]:
        return [
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
            "--model-summary",
            "qwen/qwen3.8-27b",
            "--model-scoring",
            "qwen/qwen3.8-27b",
            "--openrouter-api-key",
            "test-key",
        ]

    def test_help_lists_options(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "--issue" in result.output
        assert "--timeout-seconds" in result.output

    def test_evaluates_each_target_and_reports_ok(self, monkeypatch) -> None:
        runner = CliRunner()
        run_loop = AsyncMock()
        monkeypatch.setattr("scripts.llm.canary.run_evaluate_loop", run_loop)

        result = runner.invoke(
            cli,
            [*self._base_args(), "--issue", "snapcraft:6381", "--issue", "debcraft:41"],
        )

        assert result.exit_code == 0
        assert run_loop.await_count == 2
        assert "snapcraft#6381: ok" in result.output
        assert "debcraft#41: ok" in result.output

    def test_passes_force_and_single_issue_semantics(self, monkeypatch) -> None:
        runner = CliRunner()
        run_loop = AsyncMock()
        monkeypatch.setattr("scripts.llm.canary.run_evaluate_loop", run_loop)

        runner.invoke(cli, [*self._base_args(), "--issue", "snapcraft:6381"])

        _, kwargs = run_loop.call_args
        assert kwargs["project"] == "snapcraft"
        assert kwargs["issue"] == "6381"
        assert kwargs["force"] is True
        assert kwargs["limit"] == 1
        assert kwargs["concurrency"] == 1

    def test_stops_batch_after_first_error(self, monkeypatch) -> None:
        runner = CliRunner()
        run_loop = AsyncMock(side_effect=[None, RuntimeError("boom")])
        monkeypatch.setattr("scripts.llm.canary.run_evaluate_loop", run_loop)

        result = runner.invoke(
            cli,
            [
                *self._base_args(),
                "--issue",
                "snapcraft:6381",
                "--issue",
                "debcraft:41",
                "--issue",
                "craft-parts:766",
            ],
        )

        assert result.exit_code == 1
        assert run_loop.await_count == 2  # never reaches the third target
        assert "snapcraft#6381: ok" in result.output
        assert "debcraft#41: error: boom" in result.output
        assert "craft-parts#766" not in result.output

    def test_timeout_reported_and_stops_batch(self, monkeypatch) -> None:
        runner = CliRunner()

        async def _hangs(**_kwargs: object) -> None:
            await asyncio.sleep(10)

        monkeypatch.setattr("scripts.llm.canary.run_evaluate_loop", _hangs)

        result = runner.invoke(
            cli,
            [
                *self._base_args(),
                "--timeout-seconds",
                "1",
                "--issue",
                "snapcraft:6381",
                "--issue",
                "debcraft:41",
            ],
        )

        assert result.exit_code == 1
        assert "snapcraft#6381: timeout" in result.output
        assert "debcraft#41" not in result.output
