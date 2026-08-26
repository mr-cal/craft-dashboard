"""Fixtures for commit_scanner tests: a real git repo with issue-referencing commits."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import pathlib


def _run_git(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def scanner_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A real repo with two commits after an initial one, for scan-window tests."""
    repo = tmp_path / "scanner-repo"
    repo.mkdir()
    _run_git("init", "-q", "--initial-branch=main", cwd=repo)
    _run_git("config", "user.email", "test@example.com", cwd=repo)
    _run_git("config", "user.name", "Test User", cwd=repo)

    (repo / "a.py").write_text("x = 1\n")
    _run_git("add", "-A", cwd=repo)
    _run_git("commit", "-q", "-m", "Initial commit", cwd=repo)

    return repo


def commit(repo: pathlib.Path, *, filename: str, content: str, message: str) -> str:
    """Write filename=content, commit with message, return the new commit SHA."""
    (repo / filename).write_text(content)
    _run_git("add", "-A", cwd=repo)
    _run_git("commit", "-q", "-m", message, cwd=repo)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
