"""Shared fixtures for git_mirrors tests: a small real git repo in tmp_path."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import pathlib


def _run_git(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def sample_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a small real (non-bare) git repo with a few commits and files.

    Returns the working-tree path. Use bare_mirror_of() to build the bare
    mirror form that reader.py actually operates against.
    """
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
def bare_mirror(tmp_path: pathlib.Path, sample_repo: pathlib.Path) -> pathlib.Path:
    """Return a bare --mirror clone of sample_repo, named <project>.git.

    This is the exact on-disk shape reader.py and sync.py operate against —
    a bare mirror under a mirror_dir, never a working tree.
    """
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
def sample_repo_shas(sample_repo: pathlib.Path) -> list[str]:
    """Return the two commit SHAs in sample_repo, oldest first."""
    result = subprocess.run(
        ["git", "log", "--format=%H", "--reverse"],
        cwd=sample_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().splitlines()
