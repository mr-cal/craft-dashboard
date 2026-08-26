"""Unit tests for craft_dashboard.git_mirrors.sync."""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING

import pytest
from craft_dashboard.git_mirrors.sync import sync_mirror, sync_mirrors

if TYPE_CHECKING:
    import pathlib


def _run_git(
    *args: str, cwd: pathlib.Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


class TestSyncMirror:
    """Tests for syncing a single project's mirror."""

    async def test_clones_missing_mirror(
        self, tmp_path: pathlib.Path, sample_repo: pathlib.Path
    ) -> None:
        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        result = await sync_mirror(
            "sample-project", clone_url=str(sample_repo), mirror_dir=mirror_dir
        )
        assert result.status == "cloned"
        mirror_path = mirror_dir / "sample-project.git"
        assert mirror_path.exists()
        # gc.auto=0 must be configured on every new mirror (design doc
        # section 2: "Mirrors set gc.auto=0").
        config = await asyncio.to_thread(
            _run_git,
            "-C",
            str(mirror_path),
            "config",
            "gc.auto",
        )
        assert config.stdout.strip() == "0"
        # Reflogs MUST be enabled. Bare repos default core.logAllRefUpdates
        # to unset (reflogs OFF), so this only holds if _configure_mirror
        # sets it explicitly. It is load-bearing: with fetch --prune, a
        # force-push/rebase or deleted PR head moves a ref, and a later
        # scheduled gc would otherwise drop the old commit — including a SHA
        # an evaluation pinned. The reflog keeps that commit reachable, so
        # recorded evidence stays reproducible (design sections 2 and 3).
        reflog = await asyncio.to_thread(
            _run_git,
            "-C",
            str(mirror_path),
            "config",
            "core.logAllRefUpdates",
        )
        assert reflog.stdout.strip() == "true"

    async def test_existing_mirror_config_is_healed_on_resync(
        self, tmp_path: pathlib.Path, sample_repo: pathlib.Path
    ) -> None:
        # A mirror that predates a config setting (simulated by clearing it)
        # must be repaired on the next sync — the bootstrap is self-healing,
        # not clone-time-only.
        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        await sync_mirror(
            "sample-project", clone_url=str(sample_repo), mirror_dir=mirror_dir
        )
        mirror_path = mirror_dir / "sample-project.git"
        await asyncio.to_thread(
            _run_git,
            "-C",
            str(mirror_path),
            "config",
            "--unset",
            "core.logAllRefUpdates",
        )
        await sync_mirror(
            "sample-project", clone_url=str(sample_repo), mirror_dir=mirror_dir
        )
        reflog = await asyncio.to_thread(
            _run_git,
            "-C",
            str(mirror_path),
            "config",
            "core.logAllRefUpdates",
        )
        assert reflog.stdout.strip() == "true"

    async def test_fetches_new_commits_on_resync(
        self, tmp_path: pathlib.Path, sample_repo: pathlib.Path
    ) -> None:
        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        await sync_mirror(
            "sample-project", clone_url=str(sample_repo), mirror_dir=mirror_dir
        )

        # Add a new commit upstream, then sync again — must fetch it.
        (sample_repo / "new_file.py").write_text("x = 1\n")
        await asyncio.to_thread(_run_git, "add", "-A", cwd=sample_repo)
        await asyncio.to_thread(
            _run_git, "commit", "-q", "-m", "Add new_file", cwd=sample_repo
        )

        result = await sync_mirror(
            "sample-project", clone_url=str(sample_repo), mirror_dir=mirror_dir
        )
        assert result.status == "fetched"

        log = await asyncio.to_thread(
            _run_git,
            "-C",
            str(mirror_dir / "sample-project.git"),
            "log",
            "--oneline",
        )
        assert "Add new_file" in log.stdout

    async def test_corrupt_existing_directory_is_recloned(
        self, tmp_path: pathlib.Path, sample_repo: pathlib.Path
    ) -> None:
        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        corrupt_path = mirror_dir / "sample-project.git"
        corrupt_path.mkdir()
        (corrupt_path / "not-a-repo").write_text("broken\n")

        result = await sync_mirror(
            "sample-project", clone_url=str(sample_repo), mirror_dir=mirror_dir
        )

        assert result.status == "cloned"
        bare = await asyncio.to_thread(
            _run_git,
            "-C",
            str(corrupt_path),
            "rev-parse",
            "--is-bare-repository",
        )
        assert bare.stdout.strip() == "true"

    async def test_is_idempotent(
        self, tmp_path: pathlib.Path, sample_repo: pathlib.Path
    ) -> None:
        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        first = await sync_mirror(
            "sample-project", clone_url=str(sample_repo), mirror_dir=mirror_dir
        )
        second = await sync_mirror(
            "sample-project", clone_url=str(sample_repo), mirror_dir=mirror_dir
        )
        assert first.status == "cloned"
        assert second.status == "fetched"  # no new commits, but no error either

    @pytest.mark.skipif(
        __import__("os").environ.get("CRAFT_DASHBOARD_OFFLINE_TESTS") == "1",
        reason="requires network access to attempt a real (failing) clone",
    )
    async def test_renamed_or_archived_project_is_logged_and_skipped(
        self, tmp_path: pathlib.Path
    ) -> None:
        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        result = await sync_mirror(
            "nonexistent-project",
            clone_url="https://github.com/canonical/definitely-not-a-real-repo-xyz.git",
            mirror_dir=mirror_dir,
        )
        assert result.status == "skipped"
        assert not (mirror_dir / "nonexistent-project.git").exists()


class TestSyncMirrors:
    """Tests for the bulk mirrors-sync entry point used by the CLI."""

    async def test_syncs_every_allowed_project(
        self, tmp_path: pathlib.Path, sample_repo: pathlib.Path
    ) -> None:
        mirror_dir = tmp_path / "mirrors"
        mirror_dir.mkdir()
        results = await sync_mirrors(
            allowed_projects={"sample-project": "canonical"},
            mirror_dir=mirror_dir,
            clone_url_override={"sample-project": str(sample_repo)},
        )
        assert results["sample-project"].status == "cloned"
