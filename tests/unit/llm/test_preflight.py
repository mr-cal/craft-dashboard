"""Unit tests for craft_dashboard.llm.preflight.run_preflight."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest
from craft_dashboard.llm.preflight import run_preflight

if TYPE_CHECKING:
    from pathlib import Path


def _run_git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


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


@dataclass
class _BareMirrorFixture:
    mirror_dir: Path
    shas: dict[str, str]


@pytest.fixture
def bare_mirror_with_pinned_sha(tmp_path: Path) -> _BareMirrorFixture:
    mirror_dir = tmp_path / "mirrors"
    mirror_dir.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    mirror = mirror_dir / "rockcraft.git"

    _run_git("init", "-q", "--initial-branch=main", cwd=worktree)
    _run_git("config", "user.email", "test@example.com", cwd=worktree)
    _run_git("config", "user.name", "Test User", cwd=worktree)
    (worktree / "README.md").write_text("# rockcraft\n")
    _run_git("add", "-A", cwd=worktree)
    _run_git("commit", "-q", "-m", "Initial commit", cwd=worktree)

    sha = _run_git("rev-parse", "HEAD", cwd=worktree)
    subprocess.run(
        ["git", "clone", "--mirror", "-q", str(worktree), str(mirror)],
        check=True,
        capture_output=True,
    )
    return _BareMirrorFixture(mirror_dir=mirror_dir, shas={"rockcraft": sha})


class _SpyLLM:
    """A stand-in LLM client that fails the test if it is ever called."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("preflight must not spend LLM tokens")


class TestRunPreflight:
    async def test_missing_sha_that_cannot_be_fetched_releases_claim_zero_llm_calls(
        self, tmp_path: Path
    ) -> None:
        llm = _SpyLLM()
        release = AsyncMock()
        sync_mirror = AsyncMock(return_value=False)

        result = await run_preflight(
            claim={
                "issue_id": 1,
                "project_name": "rockcraft",
                "repo_shas": {"rockcraft": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"},
            },
            mirror_dir=tmp_path,
            llm=llm,
            sync_mirror=sync_mirror,
            release_claim=release,
            check_related_endpoint=AsyncMock(return_value=True),
        )

        assert result.ok is False
        assert result.reason == "missing_sha"
        sync_mirror.assert_awaited_once_with("rockcraft")
        release.assert_awaited_once_with(issue_id=1, reason="missing_sha")
        assert llm.calls == 0

    async def test_dead_related_endpoint_releases_claim_zero_llm_calls(
        self,
        bare_mirror_with_pinned_sha: _BareMirrorFixture,
    ) -> None:
        llm = _SpyLLM()
        release = AsyncMock()

        result = await run_preflight(
            claim={
                "issue_id": 1,
                "project_name": "rockcraft",
                "repo_shas": bare_mirror_with_pinned_sha.shas,
            },
            mirror_dir=bare_mirror_with_pinned_sha.mirror_dir,
            llm=llm,
            sync_mirror=AsyncMock(return_value=True),
            release_claim=release,
            check_related_endpoint=AsyncMock(side_effect=httpx.ConnectError("boom")),
        )

        assert result.ok is False
        assert result.reason == "related_endpoint_unreachable"
        release.assert_awaited_once_with(
            issue_id=1, reason="related_endpoint_unreachable"
        )
        assert llm.calls == 0

    async def test_all_present_passes_without_releasing(
        self,
        bare_mirror_with_pinned_sha: _BareMirrorFixture,
    ) -> None:
        llm = _SpyLLM()
        release = AsyncMock()

        result = await run_preflight(
            claim={
                "issue_id": 1,
                "project_name": "rockcraft",
                "repo_shas": bare_mirror_with_pinned_sha.shas,
            },
            mirror_dir=bare_mirror_with_pinned_sha.mirror_dir,
            llm=llm,
            sync_mirror=AsyncMock(return_value=True),
            release_claim=release,
            check_related_endpoint=AsyncMock(return_value=True),
        )

        assert result.ok is True
        assert result.reason is None
        release.assert_not_awaited()
        assert llm.calls == 0
