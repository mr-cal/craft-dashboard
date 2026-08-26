"""Unit tests for craft_dashboard.git_mirrors.reader."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING

import pytest
from craft_dashboard.git_mirrors import reader as reader_module
from craft_dashboard.git_mirrors.exceptions import InvalidPathError, InvalidRefError
from craft_dashboard.git_mirrors.reader import (
    _summarize_layout,
    grep_repo,
    head_sha,
    read_file,
    repo_layout,
    validate_path,
    validate_ref,
)

if TYPE_CHECKING:
    import pathlib


class TestValidateRef:
    """A ref must be a 40-character hex SHA."""

    def test_valid_sha_passes(self) -> None:
        validate_ref("a" * 40)  # does not raise

    def test_short_sha_rejected(self) -> None:
        with pytest.raises(InvalidRefError):
            validate_ref("abc123")

    def test_branch_name_rejected(self) -> None:
        with pytest.raises(InvalidRefError):
            validate_ref("main")

    def test_non_hex_rejected(self) -> None:
        with pytest.raises(InvalidRefError):
            validate_ref("g" * 40)


class TestValidatePath:
    """A path must be relative and must not traverse outside the repo."""

    def test_relative_path_passes(self) -> None:
        validate_path("src/parts.py")  # does not raise

    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(InvalidPathError):
            validate_path("/etc/passwd")

    def test_dotdot_traversal_rejected(self) -> None:
        with pytest.raises(InvalidPathError):
            validate_path("../../etc/passwd")

    def test_embedded_dotdot_rejected(self) -> None:
        with pytest.raises(InvalidPathError):
            validate_path("src/../../etc/passwd")


class TestHeadSha:
    """head_sha() wraps `git rev-parse --verify HEAD` for external callers."""

    async def test_returns_full_sha_matching_head(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        sha = await head_sha(bare_mirror)
        assert sha == sample_repo_shas[-1]
        assert len(sha) == 40

    async def test_unborn_head_raises(self, tmp_path: pathlib.Path) -> None:
        empty_mirror = tmp_path / "empty.git"
        subprocess.run(  # noqa: ASYNC221 - one-off bare-repo setup, not a hot path
            ["git", "init", "-q", "--bare", str(empty_mirror)],
            check=True,
            capture_output=True,
        )
        with pytest.raises(subprocess.CalledProcessError):
            await head_sha(empty_mirror)


class TestReadFile:
    """read_file() runs `git show <ref>:<path>` against a bare mirror."""

    async def test_reads_file_content_at_ref(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        content = await read_file(
            bare_mirror, path="src/parts.py", ref=sample_repo_shas[-1]
        )
        assert "is not a valid part name" in content

    async def test_unknown_path_raises(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            await read_file(
                bare_mirror, path="does/not/exist.py", ref=sample_repo_shas[-1]
            )

    async def test_absolute_path_rejected(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        with pytest.raises(InvalidPathError):
            await read_file(bare_mirror, path="/etc/passwd", ref=sample_repo_shas[-1])

    async def test_non_sha_ref_rejected(self, bare_mirror: pathlib.Path) -> None:
        with pytest.raises(InvalidRefError):
            await read_file(bare_mirror, path="src/parts.py", ref="main")


class TestGrepRepo:
    """grep_repo() runs `git grep -e <pattern> <ref>`."""

    async def test_finds_literal_match(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        hits = await grep_repo(
            bare_mirror, pattern="is not a valid part name", ref=sample_repo_shas[-1]
        )
        assert any("parts.py" in hit for hit in hits)

    async def test_pattern_starting_with_dash_is_not_parsed_as_flag(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        # A pattern beginning with "-" must never be interpretable as a git
        # grep flag — `-e` before the pattern guarantees this.
        hits = await grep_repo(
            bare_mirror, pattern="--destructive-mode", ref=sample_repo_shas[-1]
        )
        assert hits == []  # no matches, but must not raise or behave as a flag

    async def test_no_matches_returns_empty_list(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        hits = await grep_repo(
            bare_mirror, pattern="nonexistent_xyz_pattern", ref=sample_repo_shas[-1]
        )
        assert hits == []

    async def test_output_is_capped_at_the_byte_ceiling(
        self, tmp_path: pathlib.Path, monkeypatch
    ) -> None:
        # A repo whose matching output far exceeds _MAX_OUTPUT_BYTES must not
        # buffer unbounded memory: grep_repo streams under the ceiling and
        # returns truncated output rather than the whole thing.
        # Lower the ceiling so the test repo need not be huge.
        monkeypatch.setattr(reader_module, "_MAX_OUTPUT_BYTES", 2_000)

        repo = tmp_path / "big"
        repo.mkdir()
        reader_module.subprocess.run(
            ["git", "init", "-q", "--initial-branch=main"], cwd=repo, check=True
        )
        reader_module.subprocess.run(
            ["git", "config", "user.email", "t@e.com"], cwd=repo, check=True
        )
        reader_module.subprocess.run(
            ["git", "config", "user.name", "t"], cwd=repo, check=True
        )
        # 5000 matching lines >> the 2000-byte ceiling.
        (repo / "big.txt").write_text("MATCHME line\n" * 5000)
        reader_module.subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        reader_module.subprocess.run(
            ["git", "commit", "-q", "-m", "big"], cwd=repo, check=True
        )
        sha = reader_module.subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        mirror = tmp_path / "big.git"
        reader_module.subprocess.run(
            ["git", "clone", "--mirror", "-q", str(repo), str(mirror)], check=True
        )

        hits = await grep_repo(mirror, pattern="MATCHME", ref=sha)
        # The captured output was bounded, so far fewer than 5000 lines
        # survive — the ceiling held rather than returning everything.
        assert 0 < len(hits) < 5000
        assert sum(len(h) for h in hits) <= reader_module._MAX_OUTPUT_BYTES


class TestRepoLayout:
    """repo_layout() summarizes `git ls-tree -r` to a depth-2 directory count.

    The key is a file's *containing directory* (its parent), capped at two
    path segments — NOT the first two segments of the file path itself. A
    depth-1 file like ``src/parts.py`` buckets under ``src/`` (its parent),
    never under ``src/parts.py/``. Top-level files (no directory) bucket
    under the explicit root key ``./`` so they are counted rather than
    silently dropped.
    """

    async def test_returns_directory_file_counts(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        layout = await repo_layout(bare_mirror, ref=sample_repo_shas[-1])
        assert "src/" in layout
        assert layout["src/"] == 2  # parts.py, executor.py

    async def test_top_level_files_bucket_under_root(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str]
    ) -> None:
        # README.md sits at the repo root and must be counted under "./",
        # not dropped.
        layout = await repo_layout(bare_mirror, ref=sample_repo_shas[-1])
        assert layout["./"] == 1  # README.md

    async def test_deeply_nested_file_collapses_to_depth_two(
        self, tmp_path: pathlib.Path
    ) -> None:
        # A file three or more directories deep counts against its depth-2
        # ancestor, e.g. "a/b/c/d.py" -> "a/b/".
        counts = _summarize_layout(["a/b/c/d.py", "a/b/e.py", "a/f.py", "top.md"])
        assert counts == {"a/b/": 2, "a/": 1, "./": 1}

    async def test_non_sha_ref_rejected(self, bare_mirror: pathlib.Path) -> None:
        with pytest.raises(InvalidRefError):
            await repo_layout(bare_mirror, ref="main")


class TestGitSemaphore:
    """The module-level semaphore bounds concurrent git subprocesses."""

    async def test_peak_concurrency_never_exceeds_bound(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str], monkeypatch
    ) -> None:
        lock = threading.Lock()
        state = {"current": 0, "peak": 0}
        real_exec = reader_module._exec_git_capped

        def tracking_exec(*args, **kwargs):
            # _exec_git_capped is the blocking body run in a worker thread
            # under the semaphore, so counting entries/exits here measures
            # exactly how many git subprocesses overlap.
            with lock:
                state["current"] += 1
                state["peak"] = max(state["peak"], state["current"])
            try:
                # Hold the slot long enough that, without the semaphore, all
                # dispatched calls would overlap and drive peak to N.
                time.sleep(0.05)
                return real_exec(*args, **kwargs)
            finally:
                with lock:
                    state["current"] -= 1

        monkeypatch.setattr(reader_module, "_exec_git_capped", tracking_exec)

        reader_module.set_git_concurrency(2)
        try:
            await asyncio.gather(
                *[
                    read_file(
                        bare_mirror, path="src/parts.py", ref=sample_repo_shas[-1]
                    )
                    for _ in range(6)
                ]
            )
        finally:
            reader_module.set_git_concurrency(2)  # restore default for other tests

        # The invariant: peak simultaneous subprocesses never exceeded the
        # semaphore bound, even though six reads were dispatched at once.
        assert state["peak"] <= 2
        # Sanity check that the reads really did run concurrently (i.e. the
        # test would have caught an unbounded implementation): with a bound
        # of 2 and six overlapping calls, peak should actually reach 2.
        assert state["peak"] == 2

    async def test_bound_of_one_serializes(
        self, bare_mirror: pathlib.Path, sample_repo_shas: list[str], monkeypatch
    ) -> None:
        lock = threading.Lock()
        state = {"current": 0, "peak": 0}
        real_exec = reader_module._exec_git_capped

        def tracking_exec(*args, **kwargs):
            with lock:
                state["current"] += 1
                state["peak"] = max(state["peak"], state["current"])
            try:
                time.sleep(0.05)
                return real_exec(*args, **kwargs)
            finally:
                with lock:
                    state["current"] -= 1

        monkeypatch.setattr(reader_module, "_exec_git_capped", tracking_exec)

        reader_module.set_git_concurrency(1)
        try:
            await asyncio.gather(
                *[
                    read_file(
                        bare_mirror, path="src/parts.py", ref=sample_repo_shas[-1]
                    )
                    for _ in range(4)
                ]
            )
        finally:
            reader_module.set_git_concurrency(2)  # restore default for other tests

        assert state["peak"] == 1  # strictly serialized


class TestExecGitCapped:
    """Low-level subprocess execution is bounded in time and memory."""

    def test_timeout_applies_while_draining_pipes(self, monkeypatch) -> None:
        monkeypatch.setattr(reader_module, "_GIT_TIMEOUT_SECONDS", 0.5)

        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired) as exc_info:
            reader_module._exec_git_capped(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, sys, time; "
                        "sys.stdout.write(str(os.getpid())); "
                        "sys.stdout.flush(); "
                        "time.sleep(5)"
                    ),
                ]
            )
        elapsed = time.monotonic() - started

        assert elapsed < 1.5

        assert exc_info.value.output is not None
        child_pid = int(exc_info.value.output.decode().strip())
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:  # pragma: no cover - regression guard
            pytest.fail("timed out child process was not killed")

    def test_stderr_capture_is_capped(self, monkeypatch) -> None:
        monkeypatch.setattr(reader_module, "_MAX_STDERR_BYTES", 200)

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            reader_module._exec_git_capped(
                [
                    sys.executable,
                    "-c",
                    ("import sys; sys.stderr.write('E' * 5000); sys.exit(7)"),
                ]
            )

        assert exc_info.value.returncode == 7
        assert exc_info.value.stderr is not None
        assert len(exc_info.value.stderr) == reader_module._MAX_STDERR_BYTES
