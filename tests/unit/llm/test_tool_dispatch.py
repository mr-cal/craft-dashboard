"""Unit tests for craft_dashboard.llm.tool_dispatch.dispatch_tool_call."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from craft_dashboard.llm.tool_dispatch import ToolContext, dispatch_tool_call

if TYPE_CHECKING:
    from pathlib import Path


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def allow_bare_git_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow plain `git -C <bare>` commands in tests on hardened Git builds."""
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    for index in range(count):
        value = os.environ.get(f"GIT_CONFIG_VALUE_{index}")
        monkeypatch.setenv(
            f"GIT_CONFIG_KEY_{index}", os.environ[f"GIT_CONFIG_KEY_{index}"]
        )
        monkeypatch.setenv(
            f"GIT_CONFIG_VALUE_{index}",
            "" if value is None else value,
        )
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(count + 1))
    monkeypatch.setenv(f"GIT_CONFIG_KEY_{count}", "safe.bareRepository")
    monkeypatch.setenv(f"GIT_CONFIG_VALUE_{count}", "all")


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Build a small real git repo with a few commits and files."""
    repo = tmp_path / "sample-repo"
    repo.mkdir()
    _run_git("init", "-q", "--initial-branch=main", cwd=repo)
    _run_git("config", "user.email", "test@example.com", cwd=repo)
    _run_git("config", "user.name", "Test User", cwd=repo)

    (repo / "README.md").write_text("# Sample project\n")
    (repo / "src").mkdir()
    (repo / "src" / "parts.py").write_text(
        "def validate_part_name(name):\n"
        "    if not name:\n"
        "        raise ValueError('is not a valid part name')\n"
    )
    _run_git("add", "-A", cwd=repo)
    _run_git("commit", "-q", "-m", "Initial commit", cwd=repo)

    (repo / "src" / "executor.py").write_text("def run():\n    pass\n")
    _run_git("add", "-A", cwd=repo)
    _run_git("commit", "-q", "-m", "Add executor module, fixes #42", cwd=repo)

    return repo


@pytest.fixture
def bare_mirror(tmp_path: Path, sample_repo: Path) -> Path:
    """Return a bare --mirror clone of sample_repo named <project>.git."""
    mirror_dir = tmp_path / "mirrors"
    mirror_dir.mkdir()
    mirror = mirror_dir / "sample-project.git"
    subprocess.run(
        ["git", "clone", "--mirror", "-q", str(sample_repo), str(mirror)],
        check=True,
        capture_output=True,
    )
    return mirror


@pytest.fixture
def sample_repo_shas(sample_repo: Path) -> list[str]:
    """Return the repo's commit SHAs, oldest first."""
    result = subprocess.run(
        ["git", "log", "--format=%H", "--reverse"],
        cwd=sample_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().splitlines()


@pytest.fixture
def tool_context(bare_mirror: Path, sample_repo_shas: list[str]) -> ToolContext:
    """Build a ToolContext using the test mirror fixtures."""
    return ToolContext(
        mirror_dir=bare_mirror.parent,
        allowed_projects={"sample-project": "canonical"},
        pinned_shas={"sample-project": sample_repo_shas[-1]},
        eval_server_base_url="http://testserver",
        eval_api_token="test-token",  # noqa: S106
        issue_id=1,
    )


class TestDispatchToolCall:
    """dispatch_tool_call() routes a tool name to its implementation."""

    async def test_read_file_uses_pinned_sha_when_ref_omitted(
        self, tool_context: ToolContext
    ) -> None:
        result = await dispatch_tool_call(
            tool_context,
            name="read_file",
            arguments={"project": "sample-project", "path": "src/parts.py"},
        )
        assert "is not a valid part name" in result

    async def test_repo_layout_returns_directory_counts(
        self, tool_context: ToolContext
    ) -> None:
        result = await dispatch_tool_call(
            tool_context,
            name="repo_layout",
            arguments={"project": "sample-project"},
        )
        assert "src/" in result

    async def test_unknown_project_returns_error_string_not_exception(
        self, tool_context: ToolContext
    ) -> None:
        result = await dispatch_tool_call(
            tool_context,
            name="read_file",
            arguments={"project": "not-real", "path": "x.py"},
        )
        assert "error" in result.lower() or "unknown" in result.lower()

    async def test_unknown_tool_name_returns_error_string(
        self, tool_context: ToolContext
    ) -> None:
        result = await dispatch_tool_call(
            tool_context, name="delete_everything", arguments={}
        )
        assert "error" in result.lower() or "unknown" in result.lower()

    async def test_malformed_arguments_returns_error_string(
        self, tool_context: ToolContext
    ) -> None:
        result = await dispatch_tool_call(
            tool_context,
            name="read_file",
            arguments={"project": "sample-project"},
        )
        assert "error" in result.lower() or "missing" in result.lower()

    async def test_ref_overriding_pinned_sha_must_still_be_a_valid_sha(
        self, tool_context: ToolContext
    ) -> None:
        result = await dispatch_tool_call(
            tool_context,
            name="read_file",
            arguments={
                "project": "sample-project",
                "path": "src/parts.py",
                "ref": "main",
            },
        )
        assert "error" in result.lower()

    async def test_successful_file_tool_records_repo_path_pair(
        self, tool_context: ToolContext
    ) -> None:
        assert tool_context.touched_paths == set()
        await dispatch_tool_call(
            tool_context,
            name="read_file",
            arguments={"project": "sample-project", "path": "README.md"},
        )
        assert ("canonical/sample-project", "README.md") in tool_context.touched_paths

    async def test_failed_file_tool_records_no_pair(
        self, tool_context: ToolContext
    ) -> None:
        await dispatch_tool_call(
            tool_context,
            name="read_file",
            arguments={"project": "not-real", "path": "x.py"},
        )
        assert tool_context.touched_paths == set()

    async def test_grep_repo_records_matched_file_path_not_ref_sha(
        self, tool_context: ToolContext
    ) -> None:
        result = await dispatch_tool_call(
            tool_context,
            name="grep_repo",
            arguments={"pattern": "is not a valid part name"},
        )

        assert "src/parts.py" in result
        assert (
            "canonical/sample-project",
            "src/parts.py",
        ) in tool_context.touched_paths
        assert not any(
            path == tool_context.pinned_shas["sample-project"]
            for _repo, path in tool_context.touched_paths
        )

    async def test_valid_format_but_unpinned_ref_is_rejected(
        self, tool_context: ToolContext
    ) -> None:
        with patch(
            "craft_dashboard.llm.tool_dispatch.reader.read_file",
            new_callable=AsyncMock,
        ) as mock_read_file:
            mock_read_file.return_value = "unexpected success"
            result = await dispatch_tool_call(
                tool_context,
                name="read_file",
                arguments={
                    "project": "sample-project",
                    "path": "src/parts.py",
                    "ref": "1" * 40,
                },
            )

        assert "error" in result.lower()
        assert "pinned" in result.lower()

    async def test_string_false_pickaxe_uses_log_search_not_pickaxe(
        self, tool_context: ToolContext
    ) -> None:
        with (
            patch(
                "craft_dashboard.llm.tool_dispatch.reader.log_search",
                new_callable=AsyncMock,
            ) as mock_log_search,
            patch(
                "craft_dashboard.llm.tool_dispatch.reader.log_pickaxe",
                new_callable=AsyncMock,
            ) as mock_log_pickaxe,
        ):
            mock_log_search.return_value = ["abc1234 matching commit"]
            mock_log_pickaxe.return_value = ["def5678 wrong path"]

            result = await dispatch_tool_call(
                tool_context,
                name="git_log_search",
                arguments={"query": "executor", "pickaxe": "false"},
            )

        assert "abc1234 matching commit" in result
        mock_log_search.assert_awaited_once()
        mock_log_pickaxe.assert_not_awaited()

    async def test_read_file_passes_start_and_end_line_to_reader(
        self, tool_context: ToolContext
    ) -> None:
        result = await dispatch_tool_call(
            tool_context,
            name="read_file",
            arguments={
                "project": "sample-project",
                "path": "src/parts.py",
                "start_line": 2,
                "end_line": 2,
            },
        )
        assert "if not name" in result
        assert "raise ValueError" not in result

    async def test_grep_repo_truncates_at_max_matches_and_notes_it(
        self, tool_context: ToolContext
    ) -> None:
        many_hits = [f"src/file.py:{i}:match" for i in range(60)]
        with patch(
            "craft_dashboard.llm.tool_dispatch.reader.grep_repo",
            new_callable=AsyncMock,
            return_value=many_hits,
        ):
            result = await dispatch_tool_call(
                tool_context,
                name="grep_repo",
                arguments={"pattern": "match"},
            )

        assert result.count("src/file.py:") == 50
        assert "truncated at 50 matches" in result

    async def test_git_log_search_truncates_at_max_matches_and_notes_it(
        self, tool_context: ToolContext
    ) -> None:
        many_commits = [f"abc{i:04d} commit message" for i in range(60)]
        with patch(
            "craft_dashboard.llm.tool_dispatch.reader.log_search",
            new_callable=AsyncMock,
            return_value=many_commits,
        ):
            result = await dispatch_tool_call(
                tool_context,
                name="git_log_search",
                arguments={"query": "commit"},
            )

        assert "abc0049 commit message" in result
        assert "abc0050 commit message" not in result
        assert "truncated at 50 commits" in result

    async def test_related_issues_sends_bearer_auth_header(
        self, tool_context: ToolContext
    ) -> None:
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"results": []}

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = response
            await dispatch_tool_call(
                tool_context,
                name="related_issues",
                arguments={"query": "executor failures"},
            )

        _args, kwargs = mock_get.call_args
        assert kwargs["headers"]["Authorization"].startswith("Bearer ")
        assert kwargs["headers"]["Authorization"].endswith(tool_context.eval_api_token)
        assert kwargs["headers"]["Authorization"] != "******"
