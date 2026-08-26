"""Project-name allowlist and mirror-directory resolution.

The model (in later phases) never sees a filesystem path — it names a
project (e.g. "craft-parts"), and this module maps that name to a mirror
directory via a dict built from ``craft-projects`` in
``craft-dashboard.toml`` plus each project's GitHub org from the ``Project``
DB row. An unknown name raises ``UnknownProjectError`` rather than silently
falling through to some default location.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from craft_dashboard.git_mirrors.exceptions import UnknownProjectError

if TYPE_CHECKING:
    from pathlib import Path

#: Fallback GitHub org for a project with no matching Project DB row yet
#: (e.g. mirrors bootstrap running before the first collection pass).
_DEFAULT_GITHUB_ORG = "canonical"


def resolve_allowed_projects(
    *,
    craft_projects: list[str],
    project_orgs: dict[str, str],
) -> dict[str, str]:
    """Build the project-name -> github_org allowlist.

    Args:
        craft_projects: The ``craft-projects`` list from craft-dashboard.toml
            (``DashboardConfig.craft_projects``) — the authoritative mirror
            set.
        project_orgs: A ``{project_name: github_org}`` dict sourced from the
            ``projects`` DB table (``Project.name`` -> ``Project.github_org``).
            Entries not present here fall back to ``_DEFAULT_GITHUB_ORG``.

    Returns:
        A dict with one entry per name in craft_projects.

    """
    return {
        name: project_orgs.get(name, _DEFAULT_GITHUB_ORG) for name in craft_projects
    }


def mirror_path_for(
    project: str,
    *,
    mirror_dir: Path,
    allowed_projects: dict[str, str],
) -> Path:
    """Return the bare-mirror directory for *project*.

    Args:
        project: Project name as it appears in craft-projects.
        mirror_dir: Base directory holding all mirrors (Settings.mirror_dir).
        allowed_projects: The allowlist from resolve_allowed_projects().

    Raises:
        UnknownProjectError: if project is not in allowed_projects.

    """
    if project not in allowed_projects:
        raise UnknownProjectError(f"Unknown project: {project!r}")
    return mirror_dir / f"{project}.git"


def clone_url_for(project: str, *, allowed_projects: dict[str, str]) -> str:
    """Return the HTTPS clone URL for *project*.

    Raises:
        UnknownProjectError: if project is not in allowed_projects.

    """
    if project not in allowed_projects:
        raise UnknownProjectError(f"Unknown project: {project!r}")
    org = allowed_projects[project]
    return f"https://github.com/{org}/{project}.git"
