"""Tests for scripts.llm.cli."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner
from scripts.llm.cli import (
    EvaluateOptions,
    _clear_main,
    _main,
    _parse_issue_filter,
    cli,
)


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
        assert "--concurrency" in result.output
        assert "--no-progress" in result.output
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

    def test_forwards_concurrency_option_to_orchestrator(self, monkeypatch) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 0,
                "skipped": 0,
                "errored": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        runner = CliRunner()
        result = runner.invoke(cli, ["evaluate", "--concurrency", "4"])

        assert result.exit_code == 0
        evaluate_issues.assert_awaited_once()
        assert evaluate_issues.await_args.kwargs["concurrency"] == 4

    def test_json_summary_flag_prints_json_stats(self, monkeypatch) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 3,
                "skipped": 1,
                "errored": 0,
                "total_tokens": 500,
                "estimated_cost_usd": 0.05,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        runner = CliRunner()
        result = runner.invoke(cli, ["evaluate", "--json-summary"])

        assert result.exit_code == 0
        json_line = result.output.strip().splitlines()[-1]
        assert json.loads(json_line) == {
            "evaluated": 3,
            "skipped": 1,
            "errored": 0,
            "total_tokens": 500,
            "estimated_cost_usd": 0.05,
            "unpriced_evaluations": 0,
        }

    def test_without_json_summary_flag_prints_no_json(self, monkeypatch) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 3,
                "skipped": 1,
                "errored": 0,
                "total_tokens": 500,
                "estimated_cost_usd": 0.05,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        runner = CliRunner()
        result = runner.invoke(cli, ["evaluate"])

        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_exits_nonzero_when_errors_occurred(self, monkeypatch) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 2,
                "skipped": 0,
                "errored": 3,
                "total_tokens": 100,
                "estimated_cost_usd": 0.0,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        runner = CliRunner()
        result = runner.invoke(cli, ["evaluate"])

        assert result.exit_code == 1

    def test_exits_zero_when_no_errors_occurred(self, monkeypatch) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 2,
                "skipped": 0,
                "errored": 0,
                "total_tokens": 100,
                "estimated_cost_usd": 0.0,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        runner = CliRunner()
        result = runner.invoke(cli, ["evaluate"])

        assert result.exit_code == 0

    def test_exits_zero_when_server_side_eval_disabled_even_conceptually(
        self, monkeypatch
    ) -> None:
        """Disabled-eval early-return (stats=None) must not be mistaken for errors."""
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "false")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        runner = CliRunner()
        result = runner.invoke(cli, ["evaluate"])

        assert result.exit_code == 0

    def test_logs_estimated_cost_when_present(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 3,
                "skipped": 0,
                "errored": 0,
                "total_tokens": 900,
                "estimated_cost_usd": 0.0123,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        runner = CliRunner()
        with caplog.at_level(logging.INFO, logger="scripts.llm.cli"):
            result = runner.invoke(cli, ["evaluate"])

        assert result.exit_code == 0
        assert "$0.0123 estimated cost" in caplog.text

    def test_notes_unpriced_evaluations_in_cost_summary(
        self, monkeypatch, caplog
    ) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 2,
                "skipped": 0,
                "errored": 0,
                "total_tokens": 200,
                "estimated_cost_usd": 0.0,
                "unpriced_evaluations": 2,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        runner = CliRunner()
        with caplog.at_level(logging.INFO, logger="scripts.llm.cli"):
            result = runner.invoke(cli, ["evaluate"])

        assert result.exit_code == 0
        assert "2 evaluations excluded" in caplog.text
        assert "no pricing data" in caplog.text

    def test_no_console_passed_when_stdout_is_not_a_tty(self, monkeypatch) -> None:
        """CliRunner's captured stdout isn't a real TTY, so no progress bar."""
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 0,
                "skipped": 0,
                "errored": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        runner = CliRunner()
        result = runner.invoke(cli, ["evaluate"])

        assert result.exit_code == 0
        assert evaluate_issues.await_args.kwargs["console"] is None

    async def test_console_passed_when_stdout_is_a_tty(self, monkeypatch) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setattr("scripts.llm.cli.sys.stdout.isatty", lambda: True)

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        setup_rich_logging = MagicMock()
        monkeypatch.setattr("scripts.llm.cli.setup_rich_logging", setup_rich_logging)
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 0,
                "skipped": 0,
                "errored": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        await _main(
            EvaluateOptions(
                project="",
                limit=0,
                open_only=False,
                verbose=False,
                force=False,
                issue="",
                incomplete=False,
                stale_days=0,
                dry_run=False,
                strict_validation=False,
                no_resume=False,
                concurrency=1,
                no_progress=False,
            )
        )

        assert evaluate_issues.await_args.kwargs["console"] is not None
        setup_rich_logging.assert_called_once()

    async def test_no_progress_flag_disables_console_even_on_a_tty(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("ENABLE_SERVER_EVAL", "true")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setattr("scripts.llm.cli.sys.stdout.isatty", lambda: True)

        engine = MagicMock()
        engine.dispose = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.cli.get_engine", MagicMock(return_value=engine)
        )
        monkeypatch.setattr(
            "scripts.llm.cli.get_session_factory", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            "scripts.llm.cli.load_config",
            MagicMock(return_value=MagicMock(maintainers=[])),
        )
        monkeypatch.setattr(
            "scripts.llm.cli.create_llm_client", MagicMock(return_value=MagicMock())
        )
        setup_rich_logging = MagicMock()
        monkeypatch.setattr("scripts.llm.cli.setup_rich_logging", setup_rich_logging)
        evaluate_issues = AsyncMock(
            return_value={
                "evaluated": 0,
                "skipped": 0,
                "errored": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "unpriced_evaluations": 0,
            }
        )
        monkeypatch.setattr("scripts.llm.cli._evaluate_issues", evaluate_issues)

        await _main(
            EvaluateOptions(
                project="",
                limit=0,
                open_only=False,
                verbose=False,
                force=False,
                issue="",
                incomplete=False,
                stale_days=0,
                dry_run=False,
                strict_validation=False,
                no_resume=False,
                concurrency=1,
                no_progress=True,
            )
        )

        assert evaluate_issues.await_args.kwargs["console"] is None
        setup_rich_logging.assert_not_called()

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
