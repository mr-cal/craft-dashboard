"""Tests for scripts.run_slow_eval."""

import logging
import pathlib
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from scripts.run_slow_eval import _interruptible_sleep, build_command, main


class TestBuildCommand:
    def test_build_command_structure(self) -> None:
        run_llm_path = pathlib.Path("/app/scripts/run_llm.py")
        extra_args = ["--project", "snapcraft", "--llm-backend", "local"]

        cmd = build_command(run_llm_path, extra_args)

        assert cmd[1] == str(run_llm_path)
        assert cmd[2] == "evaluate"
        assert cmd[3:7] == ["--concurrency", "1", "--limit", "1"]
        assert cmd[7:] == extra_args


class TestInterruptibleSleep:
    def test_sleep_completes(self) -> None:
        assert _interruptible_sleep(0.01) is True


class TestRunSlowEvalCLI:
    def test_min_delay_greater_than_max_delay(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--min-delay", "30", "--max-delay", "10"])

        assert result.exit_code != 0
        assert "--min-delay cannot be greater than --max-delay" in result.output

    def test_count_iterations_and_delays(self, caplog) -> None:
        caplog.set_level(logging.INFO)
        runner = CliRunner()
        mock_proc = MagicMock(returncode=0)
        delays: list[float] = []

        def fake_sleep(duration: float) -> bool:
            delays.append(duration)
            return True

        with (
            patch("subprocess.run", return_value=mock_proc) as mock_run,
            patch("scripts.run_slow_eval._interruptible_sleep", side_effect=fake_sleep),
        ):
            result = runner.invoke(
                main,
                [
                    "--count",
                    "3",
                    "--min-delay",
                    "10.0",
                    "--max-delay",
                    "20.0",
                    "--project",
                    "snapcraft",
                ],
            )

        assert result.exit_code == 0
        assert mock_run.call_count == 3
        # Should sleep between evaluations (count - 1 times = 2 times)
        assert len(delays) == 2
        for delay in delays:
            assert 10.0 <= delay <= 20.0
        assert "Target of 3 evaluation(s) reached." in caplog.text

    def test_stop_on_error_terminates_early(self, caplog) -> None:
        runner = CliRunner()
        mock_proc = MagicMock(returncode=1)

        with (
            patch("subprocess.run", return_value=mock_proc) as mock_run,
            patch("scripts.run_slow_eval._interruptible_sleep") as mock_sleep,
        ):
            result = runner.invoke(
                main,
                ["--count", "5", "--stop-on-error"],
            )

        assert result.exit_code == 1
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()
        assert "Stopping due to error" in caplog.text

    def test_no_stop_on_error_continues(self) -> None:
        runner = CliRunner()
        mock_proc = MagicMock(returncode=1)

        with (
            patch("subprocess.run", return_value=mock_proc) as mock_run,
            patch("scripts.run_slow_eval._interruptible_sleep", return_value=True),
        ):
            result = runner.invoke(
                main,
                ["--count", "2", "--no-stop-on-error"],
            )

        assert result.exit_code == 0
        assert mock_run.call_count == 2

    def test_keyboard_interrupt_exits_cleanly(self, caplog) -> None:
        caplog.set_level(logging.INFO)
        runner = CliRunner()
        mock_proc = MagicMock(returncode=0)

        with (
            patch("subprocess.run", return_value=mock_proc),
            patch(
                "scripts.run_slow_eval._interruptible_sleep",
                side_effect=KeyboardInterrupt,
            ),
        ):
            result = runner.invoke(main, ["--count", "5"])

        assert result.exit_code == 0
        assert "Interrupted by user" in caplog.text
