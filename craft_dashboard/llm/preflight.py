"""Worker preflight checks before any LLM evaluation work starts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from craft_dashboard.git_mirrors import reader
from craft_dashboard.git_mirrors.paths import canonical_git_project_name

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path


@dataclass
class PreflightResult:
    """Outcome of worker preflight validation for a claimed issue."""

    ok: bool
    reason: str | None = None


async def run_preflight(
    *,
    claim: dict[str, Any],
    mirror_dir: Path,
    llm: object,
    sync_mirror: Callable[[str], Awaitable[bool]],
    release_claim: Callable[..., Awaitable[None]],
    check_related_endpoint: Callable[[], Awaitable[bool]],
) -> PreflightResult:
    """Validate the claimed SHAs and related endpoint before any LLM calls."""
    del llm
    repo_shas: dict[str, str] = claim.get("repo_shas") or {}

    async def _release(reason: str) -> PreflightResult:
        await release_claim(issue_id=claim["issue_id"], reason=reason)
        return PreflightResult(ok=False, reason=reason)

    for repo, sha in repo_shas.items():
        mirror_path = mirror_dir / f"{canonical_git_project_name(repo)}.git"
        if not await reader.has_commit(mirror_path, sha):
            await sync_mirror(repo)
            if not await reader.has_commit(mirror_path, sha):
                return await _release("missing_sha")

    try:
        related_ok = await check_related_endpoint()
    except Exception:  # noqa: BLE001
        return await _release("related_endpoint_unreachable")

    if not related_ok:
        return await _release("related_endpoint_unreachable")

    return PreflightResult(ok=True)
