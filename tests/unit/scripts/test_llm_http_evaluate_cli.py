"""Tests for the HTTP-backed evaluate CLI."""

from __future__ import annotations

from unittest.mock import AsyncMock

from click.testing import CliRunner
from scripts.llm.cli import cli


def test_help_lists_http_evaluate_options() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["evaluate", "--help"])

    assert result.exit_code == 0
    assert "--server" in result.output
    assert "--ca-cert" in result.output
    assert "--llm-backend" in result.output
    assert "--interval" in result.output
    assert "--concurrency" in result.output
    assert "continuous" in result.output.lower()


def test_evaluate_uses_http_worker_with_local_backend(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    run_loop = AsyncMock()
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", run_loop)

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
            "--llm-backend",
            "local",
            "--concurrency",
            "4",
            "--interval",
            "15",
        ],
    )

    assert result.exit_code == 0
    run_loop.assert_called_once()
    assert run_loop.call_args.kwargs["llm_backend"] == "local"
    assert run_loop.call_args.kwargs["concurrency"] == 4
    assert run_loop.call_args.kwargs["poll_interval"] == 15


def test_evaluate_requires_openrouter_api_key_even_for_local_backend(
    monkeypatch,
) -> None:
    runner = CliRunner()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-model")
    monkeypatch.setattr("scripts.llm.cli.run_evaluate_loop", AsyncMock())

    def _capture_run(coro):
        coro.close()

    monkeypatch.setattr("scripts.llm.cli.asyncio.run", _capture_run)

    result = runner.invoke(
        cli,
        [
            "evaluate",
            "--server",
            "http://localhost:8000",
            "--token",
            "test-token",
            "--llm-backend",
            "local",
        ],
    )

    assert result.exit_code != 0
    assert "OPENROUTER_API_KEY" in result.output
