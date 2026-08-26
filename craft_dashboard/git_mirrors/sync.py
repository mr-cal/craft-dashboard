"""Idempotent bare-mirror clone/fetch bootstrap.

The commit scanner (Phase 3) and `craft-dashboard mirrors sync` (this
module's CLI entry point) are the only callers of git fetch — the eval
worker never writes to a mirror. Safe to call repeatedly: an existing
mirror is fetched, a missing one is cloned.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from craft_dashboard.git_mirrors.paths import clone_url_for

if TYPE_CHECKING:
    import pathlib

logger = logging.getLogger(__name__)

#: The refspec that pulls GitHub's published PR-head refs onto the mirror,
#: needed so `git diff <base>...<head>` works for contributor PR branches
#: (see design doc section 5, "Contributor PR code") without ever adding a
#: contributor's fork as a remote.
_PR_REFSPEC = "+refs/pull/*/head:refs/pull/*/head"

_CLONE_TIMEOUT_SECONDS = 300
_FETCH_TIMEOUT_SECONDS = 300
_CONFIGURE_TIMEOUT_SECONDS = 60
_GIT_HARDENING = (
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "safe.bareRepository=all",
    "-c",
    "safe.directory=*",
)


@dataclass
class MirrorSyncResult:
    """Outcome of syncing one project's mirror."""

    project: str
    status: str  # "cloned", "fetched", or "skipped"
    detail: str = ""


def _git_argv(repo: pathlib.Path, *args: str) -> list[str]:
    """Build a hardened git argv for a bare mirror repo."""
    return ["git", "-C", str(repo), *_GIT_HARDENING, *args]


def _configure_mirror(mirror_path: pathlib.Path) -> None:
    """Apply mirror hardening/config that must hold on every mirror.

    Idempotent — every setting here is safe to re-apply, so this is called
    both on a fresh clone AND on every fetch of an existing mirror (see
    sync_mirror), so a mirror cloned before a config setting existed is
    healed on the next sync rather than drifting forever.

    Settings applied:

    - ``gc.auto=0`` — no automatic gc; repacking runs only on an explicit
      scheduled pass during a quiet window (design section 2). Prevents a
      background gc from pruning an object a reader is mid-traversal on.
    - ``core.logAllRefUpdates=true`` — **keep reflogs.** Bare repositories
      ship with this *unset* (reflogs OFF) by default, so it must be turned
      on explicitly. This is load-bearing: combined with ``fetch --prune``,
      a force-pushed or rebased branch (or a deleted PR head) moves a ref,
      and a later scheduled ``git gc`` would drop the old commit — including
      a SHA an evaluation pinned and recorded. The reflog keeps that commit
      reachable so recorded evidence stays reproducible (design sections 2
      and 3). Without it the SHA-pinning guarantee silently breaks.
    - the ``refs/pull/*/head`` refspec — pull GitHub's published PR heads so
      ``git diff <base>...<head>`` works for contributor branches.
    """
    subprocess.run(
        _git_argv(mirror_path, "config", "gc.auto", "0"),
        check=True,
        capture_output=True,
        timeout=_CONFIGURE_TIMEOUT_SECONDS,
    )
    subprocess.run(
        _git_argv(mirror_path, "config", "core.logAllRefUpdates", "true"),
        check=True,
        capture_output=True,
        timeout=_CONFIGURE_TIMEOUT_SECONDS,
    )
    # Reset the fetch refspecs deterministically so re-running never appends
    # a duplicate: --replace-all with no value-pattern collapses all existing
    # remote.origin.fetch lines to the single mirror refspec, then --add
    # appends the PR-head refspec. The result is exactly these two lines on
    # every run, whatever state the mirror was in before.
    subprocess.run(
        _git_argv(
            mirror_path,
            "config",
            "--replace-all",
            "remote.origin.fetch",
            "+refs/*:refs/*",
        ),
        check=True,
        capture_output=True,
        timeout=_CONFIGURE_TIMEOUT_SECONDS,
    )
    subprocess.run(
        _git_argv(
            mirror_path,
            "config",
            "--add",
            "remote.origin.fetch",
            _PR_REFSPEC,
        ),
        check=True,
        capture_output=True,
        timeout=_CONFIGURE_TIMEOUT_SECONDS,
    )


def _is_valid_mirror(mirror_path: pathlib.Path) -> bool:
    """Return whether *mirror_path* is a usable bare Git repository."""
    try:
        result = subprocess.run(
            _git_argv(mirror_path, "rev-parse", "--is-bare-repository"),
            check=True,
            capture_output=True,
            text=True,
            timeout=_CONFIGURE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() == "true"


async def sync_mirror(
    project: str, *, clone_url: str, mirror_dir: pathlib.Path
) -> MirrorSyncResult:
    """Clone *project* if missing, else fetch it. Never raises on failure.

    A project that fails to clone (renamed, archived, or otherwise
    unreachable upstream) is logged and reported as "skipped" rather than
    raising, so one bad project never aborts a bulk sync of the other 17.
    """
    mirror_path = mirror_dir / f"{project}.git"

    if mirror_path.exists():
        try:
            # Re-apply config BEFORE fetching so a mirror cloned before a
            # setting existed (e.g. reflogs, the PR refspec) is healed and
            # the fetch honours it. _configure_mirror is idempotent, so this
            # is safe to run on every sync and makes the bootstrap genuinely
            # self-healing rather than clone-time-only.
            await asyncio.to_thread(_configure_mirror, mirror_path)
            await asyncio.to_thread(
                subprocess.run,
                # --prune drops remote-tracking refs deleted upstream (e.g. a
                # merged PR head or a force-deleted branch). This is safe only
                # because core.logAllRefUpdates keeps a reflog entry for the
                # pruned ref, so a SHA an evaluation pinned stays reachable
                # until the scheduled gc (which also respects reflogs) — see
                # _configure_mirror. Without reflogs, --prune + gc would make
                # a pinned commit unreachable and break reproducibility.
                _git_argv(mirror_path, "fetch", "--prune"),
                check=True,
                capture_output=True,
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if not await asyncio.to_thread(_is_valid_mirror, mirror_path):
                logger.warning(
                    "Mirror at %s is invalid/corrupt; removing and re-cloning",
                    mirror_path,
                )
                await asyncio.to_thread(shutil.rmtree, mirror_path)
            else:
                logger.warning("Mirror fetch failed for %s: %s", project, exc)
                return MirrorSyncResult(project, "skipped", detail=str(exc))
        else:
            return MirrorSyncResult(project, "fetched")

    try:
        await asyncio.to_thread(
            subprocess.run,
            [
                "git",
                *_GIT_HARDENING,
                "clone",
                "--mirror",
                "-q",
                clone_url,
                str(mirror_path),
            ],
            check=True,
            capture_output=True,
            timeout=_CLONE_TIMEOUT_SECONDS,
        )
        await asyncio.to_thread(_configure_mirror, mirror_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "Mirror clone failed for %s (renamed/archived upstream?): %s",
            project,
            exc,
        )
        return MirrorSyncResult(project, "skipped", detail=str(exc))
    return MirrorSyncResult(project, "cloned")


async def sync_mirrors(
    *,
    allowed_projects: dict[str, str],
    mirror_dir: pathlib.Path,
    clone_url_override: dict[str, str] | None = None,
) -> dict[str, MirrorSyncResult]:
    """Sync every project in allowed_projects, sequentially.

    Sequential (not gathered/parallel) deliberately: the VPS this runs on
    has 1 vCPU and ~307MB available RAM, and multiple simultaneous large
    clones (snapcraft is 37MB) would contend for both. All 18 mirrors
    total ~150MB and this runs on a schedule, not interactively, so
    sequential is an acceptable tradeoff for lower peak resource use.

    Args:
        allowed_projects: {project_name: github_org}, from
            craft_dashboard.git_mirrors.paths.resolve_allowed_projects().
        mirror_dir: Base directory holding all mirrors.
        clone_url_override: Test-only hook to substitute a local path for
            a project's real GitHub clone URL.

    Returns:
        {project_name: MirrorSyncResult}.

    """
    await asyncio.to_thread(mirror_dir.mkdir, parents=True, exist_ok=True)
    overrides = clone_url_override or {}
    results: dict[str, MirrorSyncResult] = {}
    for project in allowed_projects:
        clone_url = overrides.get(
            project, clone_url_for(project, allowed_projects=allowed_projects)
        )
        results[project] = await sync_mirror(
            project, clone_url=clone_url, mirror_dir=mirror_dir
        )
    return results
