"""Dependency collector for tracking project dependencies across branches."""

import logging
import re
import tomllib
import urllib.request
from datetime import UTC, datetime
from typing import Any

from github import Github
from packaging.version import Version

logger = logging.getLogger(__name__)

_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)"  # package name
    r"(?:\[.*?\])?"  # optional extras
    r"(.*)"  # version spec
)


def parse_requirements_line(line: str) -> tuple[str, str] | None:
    """Parse a single requirements line into (name, version_spec).

    Args:
        line: A single line from a requirements file or pyproject.toml dependency.

    Returns:
        A tuple of (package_name, version_spec), or None for empty/comment lines.

    """
    line = line.strip()
    if not line or line.startswith(("#", "-")):
        return None

    match = _REQUIREMENT_RE.match(line)
    if not match:
        return None

    name = match.group(1)
    spec = match.group(2).strip()
    return name, spec


def parse_uv_lock(content: str) -> dict[str, str]:
    """Parse a uv.lock file and return a mapping of package name to installed version.

    Args:
        content: The decoded text content of a uv.lock file.

    Returns:
        A dict mapping normalised package name → installed version string.

    """
    data = tomllib.loads(content)
    packages: dict[str, str] = {}
    for pkg in data.get("package", []):
        name = pkg.get("name", "")
        version = pkg.get("version", "")
        if name and version:
            # Normalise name: lowercase and replace underscores/dots with dashes
            normalised = name.lower().replace("_", "-").replace(".", "-")
            packages[normalised] = version
    return packages


def _normalise_lib_name(name: str) -> str:
    """Normalise a library name for lookup in uv.lock data."""
    return name.lower().replace("_", "-").replace(".", "-")


def get_pypi_versions(name: str) -> list[str]:
    """Fetch all non-prerelease versions of a package from PyPI.

    Args:
        name: The PyPI package name.

    Returns:
        A list of version strings (non-prerelease), or an empty list on error.

    """
    url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            import json  # noqa: PLC0415
            data: dict[str, Any] = json.loads(resp.read())
        releases = data.get("releases", {})
        return [v for v in releases if not Version(v).is_prerelease]
    except Exception:  # noqa: BLE001
        logger.warning("Could not fetch PyPI versions for %s", name)
        return []


def get_latest_for_branch(
    branch_name: str,
    all_versions: list[str],
    installed_version: str,
) -> str:
    """Compute the latest applicable version for a given branch.

    For ``main`` branches, returns the globally latest version.
    For hotfix branches, returns the latest version in the same minor series.

    Args:
        branch_name: The branch name (e.g. ``"main"`` or ``"hotfix/3.5"``).
        all_versions: All known non-prerelease version strings.
        installed_version: The currently installed version string.

    Returns:
        The latest applicable version string.

    """
    versions = [Version(v) for v in all_versions if not Version(v).is_prerelease]
    if not versions:
        return installed_version
    ver = Version(installed_version)
    if branch_name == "main":
        return str(max(versions))
    # Hotfix branch — latest in the same minor series
    series_versions = [v for v in versions if (v.major, v.minor) == (ver.major, ver.minor)]
    return str(max(series_versions)) if series_versions else str(max(versions))


class DependencyCollector:
    """Collects dependency information from project repositories."""

    def __init__(
        self,
        token: str,
        org: str = "canonical",
        craft_libraries: list[str] | None = None,
    ) -> None:
        """Initialize the dependency collector.

        Args:
            token: GitHub personal access token.
            org: GitHub organization name.
            craft_libraries: Libraries whose installed versions should be resolved
                from ``uv.lock``.  If ``None``, no version resolution is attempted.

        """
        self.gh = Github(token)
        self.org = org
        self.craft_libraries: list[str] = craft_libraries or []
        # Cache PyPI version lists per package name for the lifetime of this collector.
        self._pypi_cache: dict[str, list[str]] = {}

    def _get_pypi_versions_cached(self, name: str) -> list[str]:
        """Return PyPI versions, using an in-memory cache."""
        if name not in self._pypi_cache:
            self._pypi_cache[name] = get_pypi_versions(name)
        return self._pypi_cache[name]

    async def collect_dependencies(
        self,
        repo_name: str,
        project_id: int,
        branches: list[str],
        session,  # noqa: ANN001
    ) -> int:
        """Collect dependencies for a repository across specified branches.

        Prefers ``uv.lock`` for exact installed versions.  Falls back to
        ``pyproject.toml`` if ``uv.lock`` is not present.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            branches: List of branch names to check.
            session: An async SQLAlchemy session.

        Returns:
            The number of dependencies upserted.

        """
        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

        from craft_dashboard.models.dependency import Dependency  # noqa: PLC0415

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")
        count = 0

        for branch in branches:
            # Try uv.lock first for exact installed versions.
            lock_packages: dict[str, str] | None = None
            try:
                lock_contents = repo.get_contents("uv.lock", ref=branch)
                lock_packages = parse_uv_lock(lock_contents.decoded_content.decode())
                source_file = "uv.lock"
            except Exception:  # noqa: BLE001
                source_file = "pyproject.toml"

            # Fetch pyproject.toml for the dependency list.
            try:
                contents = repo.get_contents("pyproject.toml", ref=branch)
                pyproject = tomllib.loads(contents.decoded_content.decode())
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not read pyproject.toml from %s/%s@%s",
                    self.org,
                    repo_name,
                    branch,
                )
                continue

            deps = pyproject.get("project", {}).get("dependencies", [])
            if not deps:
                # Try poetry-style dependencies
                poetry_deps = (
                    pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {})
                )
                deps = []
                for pkg, spec in poetry_deps.items():
                    if pkg.lower() == "python":
                        continue
                    if isinstance(spec, str):
                        deps.append(f"{pkg}{spec}" if spec not in ("*", "") else pkg)
                    elif isinstance(spec, dict):
                        ver = spec.get("version", "*")
                        deps.append(f"{pkg}{ver}" if ver != "*" else pkg)

            for dep_line in deps:
                parsed = parse_requirements_line(dep_line)
                if parsed is None:
                    continue
                dep_name, version_spec = parsed

                installed_version: str | None = None
                latest_version: str | None = None
                series: str | None = None
                is_outdated: bool | None = None

                if lock_packages is not None and dep_name in self.craft_libraries:
                    normalised = _normalise_lib_name(dep_name)
                    installed_version = lock_packages.get(normalised)
                    if installed_version:
                        try:
                            ver = Version(installed_version)
                            series = f"{ver.major}.{ver.minor}"
                            all_versions = self._get_pypi_versions_cached(dep_name)
                            if all_versions:
                                latest_version = get_latest_for_branch(
                                    branch, all_versions, installed_version
                                )
                                is_outdated = (
                                    Version(latest_version) > ver
                                )
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "Could not compute version info for %s in %s@%s",
                                dep_name,
                                repo_name,
                                branch,
                            )

                stmt = insert(Dependency).values(
                    project_id=project_id,
                    branch=branch,
                    dependency_name=dep_name,
                    version_spec=version_spec,
                    source_file=source_file,
                    fetched_at=datetime.now(tz=UTC),
                    installed_version=installed_version,
                    latest_version=latest_version,
                    series=series,
                    is_outdated=is_outdated,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["project_id", "branch", "dependency_name"],
                    set_={
                        "version_spec": stmt.excluded.version_spec,
                        "source_file": stmt.excluded.source_file,
                        "fetched_at": stmt.excluded.fetched_at,
                        "installed_version": stmt.excluded.installed_version,
                        "latest_version": stmt.excluded.latest_version,
                        "series": stmt.excluded.series,
                        "is_outdated": stmt.excluded.is_outdated,
                    },
                )
                await session.execute(stmt)
                count += 1

        await session.commit()
        logger.info("Collected %d dependencies from %s/%s", count, self.org, repo_name)
        return count

