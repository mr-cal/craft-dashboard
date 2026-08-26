"""Tests for the CLI entry point."""

from click.testing import CliRunner
from craft_dashboard.cli import main


class TestCLI:
    """Tests for the CLI."""

    def test_main_help(self) -> None:
        """The --help flag shows usage information."""
        runner = CliRunner()

        result = runner.invoke(main, ["--help"])

        assert result.exit_code == 0
        assert "craft-dashboard" in result.output

    def test_serve_command_exists(self) -> None:
        """The 'serve' subcommand exists."""
        runner = CliRunner()

        result = runner.invoke(main, ["serve", "--help"])

        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output

    def test_collect_command_exists(self) -> None:
        """The 'collect' subcommand exists."""
        runner = CliRunner()

        result = runner.invoke(main, ["collect", "--help"])

        assert result.exit_code == 0

    def test_mirrors_sync_help(self) -> None:
        """The 'mirrors sync' subcommand exists."""
        runner = CliRunner()

        result = runner.invoke(main, ["mirrors", "sync", "--help"])

        assert result.exit_code == 0
        assert "Clone or fetch" in result.output

    def test_commit_scanner_run_help(self) -> None:
        """The 'commit-scanner run' subcommand exists."""
        runner = CliRunner()

        result = runner.invoke(main, ["commit-scanner", "run", "--help"])

        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--top-k" in result.output
        assert "--threshold" in result.output
