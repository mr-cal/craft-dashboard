"""Tests for the CLI entry point."""

from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_commit_scanner_run_uses_embedding_key_without_fallback(
        self,
    ) -> None:
        """The commit scanner strictly uses openrouter_api_key_embedding with no fallback."""
        runner = CliRunner()
        mock_scan = AsyncMock(return_value=[])
        mock_embedding_client = MagicMock()

        # Case 1: Only openrouter_api_key is set (no embedding key).
        # Must NOT fall back: embed_client should be None.
        with (
            patch.dict(
                "os.environ",
                {
                    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
                    "OPENROUTER_API_KEY": "general-secret-key",
                    "OPENROUTER_API_KEY_EMBEDDING": "",
                },
            ),
            patch(
                "craft_dashboard.commit_scanner.scanner.scan_all_projects",
                mock_scan,
            ),
            patch(
                "craft_dashboard.llm.embeddings.EmbeddingClient",
                mock_embedding_client,
            ),
            patch("craft_dashboard.cli._load_project_orgs", AsyncMock(return_value={})),
        ):
            result = runner.invoke(main, ["commit-scanner", "run", "--dry-run"])
            assert result.exit_code == 0
            assert mock_embedding_client.call_count == 0
            _, kwargs = mock_scan.call_args
            assert kwargs["embed_client"] is None

        # Case 2: openrouter_api_key_embedding is set.
        # Should initialize EmbeddingClient with openrouter_api_key_embedding.
        mock_scan.reset_mock()
        mock_embedding_client.reset_mock()
        mock_client_instance = AsyncMock()
        mock_embedding_client.return_value = mock_client_instance

        with (
            patch.dict(
                "os.environ",
                {
                    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
                    "OPENROUTER_API_KEY": "general-secret-key",
                    "OPENROUTER_API_KEY_EMBEDDING": "embed-secret-key",
                },
            ),
            patch(
                "craft_dashboard.commit_scanner.scanner.scan_all_projects",
                mock_scan,
            ),
            patch(
                "craft_dashboard.llm.embeddings.EmbeddingClient",
                mock_embedding_client,
            ),
            patch("craft_dashboard.cli._load_project_orgs", AsyncMock(return_value={})),
        ):
            result = runner.invoke(main, ["commit-scanner", "run", "--dry-run"])
            assert result.exit_code == 0
            assert mock_embedding_client.call_count == 1
            _, client_kwargs = mock_embedding_client.call_args
            assert client_kwargs["api_key"] == "embed-secret-key"
