"""Tests for scripts.llm.cli."""

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
    def test_help_lists_strict_validation_flag(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli, ["evaluate", "--help"])

        assert result.exit_code == 0
        assert "--strict-validation" in result.output

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
