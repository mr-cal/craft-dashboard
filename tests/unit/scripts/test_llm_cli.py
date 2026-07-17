"""Tests for scripts.llm.cli."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner
from scripts.llm.cli import _clear_main, _parse_issue_filter, cli


class TestParseIssueFilter:
    def test_parses_single_ids_and_ranges(self) -> None:
        assert _parse_issue_filter("charmcraft#2687,snapcraft#100-200") == [
            ("charmcraft", 2687, 2687),
            ("snapcraft", 100, 200),
        ]

    def test_skips_invalid_items(self) -> None:
        assert _parse_issue_filter("bad,craft-parts#abc") == []


class TestClearEvaluationsCommand:
    def test_help_lists_clear_evaluations_command(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["clear-evaluations", "--help"])

        assert result.exit_code == 0
        assert "--project" in result.output
        assert "--yes" in result.output


class TestEvaluateCommand:
    def test_help_lists_validation_and_resume_flags(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["evaluate", "--help"])

        assert result.exit_code == 0
        assert "--strict-validation" in result.output
        assert "--no-resume" in result.output
        assert "--backend" not in result.output

    def test_returns_cleanly_when_server_side_eval_is_disabled(
        self, monkeypatch, caplog
    ) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "false")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        runner = CliRunner()

        with caplog.at_level(logging.INFO, logger="scripts.llm.cli"):
            result = runner.invoke(cli, ["evaluate"])

        assert result.exit_code == 0
        assert (
            "Server-side evaluation is disabled (ENABLE_SERVER_EVAL=false). "
            "Use the eval client script for pull-based evaluation instead."
        ) in caplog.text

    @pytest.mark.asyncio
    async def test_clear_main_confirms_before_deleting(self, monkeypatch) -> None:
        count_evaluations = AsyncMock(return_value=3)
        clear_evaluations = AsyncMock(return_value=3)
        confirm = MagicMock()
        engine = MagicMock()
        engine.dispose = AsyncMock()
        session_factory = MagicMock()
        monkeypatch.setattr("scripts.llm.cli.count_evaluations", count_evaluations)
        monkeypatch.setattr("scripts.llm.cli._clear_evaluations", clear_evaluations)
        monkeypatch.setattr("scripts.llm.cli.click.confirm", confirm)
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory",
            MagicMock(return_value=session_factory),
        )

        await _clear_main(project="snapcraft", yes=False)

        count_evaluations.assert_awaited_once_with(
            "snapcraft",
            session_factory=session_factory,
            engine=engine,
        )
        confirm.assert_called_once()
        clear_evaluations.assert_awaited_once_with(
            "snapcraft",
            session_factory=session_factory,
            engine=engine,
        )
        engine.dispose.assert_awaited_once()
