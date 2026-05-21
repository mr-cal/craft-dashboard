"""Dependency collector for tracking project dependencies across branches."""

import logging
import re
from datetime import UTC, datetime

from github import Github

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


class DependencyCollector:
    """Collects dependency information from project repositories."""

    def __init__(self, token: str, org: str = "canonical") -> None:
        """Initialize the dependency collector.

        Args:
            token: GitHub personal access token.
            org: GitHub organization name.

        """
        self.gh = Github(token)
        self.org = org

    async def collect_dependencies(
        self,
        repo_name: str,
        project_id: int,
        branches: list[str],
        session,  # noqa: ANN001
    ) -> int:
        """Collect dependencies for a repository across specified branches.

        Reads pyproject.toml from each branch and parses the dependencies list.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            branches: List of branch names to check.
            session: An async SQLAlchemy session.

        Returns:
            The number of dependencies upserted.

        """
        import tomllib  # noqa: PLC0415

        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

        from craft_dashboard.models.dependency import Dependency  # noqa: PLC0415

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")
        count = 0

        for branch in branches:
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
                poetry_deps = pyproject.get("tool", {}).get("poetry", {}).get("dependencies", {})
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

                stmt = insert(Dependency).values(
                    project_id=project_id,
                    branch=branch,
                    dependency_name=dep_name,
                    version_spec=version_spec,
                    source_file="pyproject.toml",
                    fetched_at=datetime.now(tz=UTC),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["project_id", "branch", "dependency_name"],
                    set_={
                        "version_spec": stmt.excluded.version_spec,
                        "source_file": stmt.excluded.source_file,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                await session.execute(stmt)
                count += 1

        await session.commit()
        logger.info("Collected %d dependencies from %s/%s", count, self.org, repo_name)
        return count
