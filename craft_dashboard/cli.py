"""CLI entry point for craft-dashboard."""

import logging
import pathlib
from typing import TYPE_CHECKING

import click
import uvicorn

if TYPE_CHECKING:
    from craft_dashboard.settings import Settings


@click.group()
def main() -> None:
    """craft-dashboard: Dashboard and insights for *craft applications."""


@main.command()
@click.option(
    "--host", default="127.0.0.1", help="Bind host (e.g., 0.0.0.0 for all interfaces)."
)
@click.option("--port", default=8000, type=int, help="Bind port.")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
def serve(*, host: str, port: int, reload: bool) -> None:
    """Start the craft-dashboard web server.

    Examples::

        craft-dashboard serve
        craft-dashboard serve --host 0.0.0.0 --port 9000
        craft-dashboard serve --reload
    """
    uvicorn.run(
        "craft_dashboard.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


@main.command()
@click.option(
    "--source",
    type=click.Choice(["github", "launchpad", "all"]),
    default="all",
    help="Data source to collect from.",
)
@click.option(
    "--config-file",
    type=click.Path(exists=True),
    default="craft-dashboard.toml",
    help="Path to configuration file.",
)
@click.option("--limit", default=0, type=int, help="Max issues per repo (0 = all).")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def collect(*, source: str, config_file: str, limit: int, verbose: bool) -> None:
    """Collect data from GitHub and Launchpad.

    Fetches issues, PRs, releases, dependencies, and generates daily
    snapshots. Typically run via cron (see scripts/collect_data.py).

    Examples::

        craft-dashboard collect
        craft-dashboard collect --source github --limit 25
        craft-dashboard collect --source launchpad
    """
    import subprocess
    import sys

    cmd = [sys.executable, "scripts/collect_data.py", "--source", source]
    if limit:
        cmd.extend(["--limit", str(limit)])
    if verbose:
        cmd.append("--verbose")
    subprocess.run(cmd, check=True)


@main.group()
def mirrors() -> None:
    """Manage bare git mirrors used by deep evaluation's git tools."""


@mirrors.command(name="sync")
@click.option(
    "--config-file",
    type=click.Path(exists=True),
    default="craft-dashboard.toml",
    help="Path to configuration file.",
)
def mirrors_sync(*, config_file: str) -> None:
    """Clone or fetch every project in craft-projects as a bare mirror.

    Idempotent: clones any project missing a mirror, fetches everything
    else. Safe to run repeatedly (e.g. from a cron job or the commit
    scanner's scheduled pass).

    Examples::

        craft-dashboard mirrors sync
    """
    import asyncio

    from craft_dashboard.config import load_config
    from craft_dashboard.git_mirrors import reader
    from craft_dashboard.git_mirrors.paths import resolve_allowed_projects
    from craft_dashboard.git_mirrors.sync import sync_mirrors
    from craft_dashboard.settings import Settings

    async def _run() -> None:
        settings = Settings()
        reader.set_git_concurrency(settings.git_concurrency)
        config = load_config(pathlib.Path(config_file))
        project_orgs = await _load_project_orgs(settings)
        allowed = resolve_allowed_projects(
            craft_projects=config.craft_projects, project_orgs=project_orgs
        )
        results = await sync_mirrors(
            allowed_projects=allowed, mirror_dir=settings.mirror_dir_path
        )
        for project, result in results.items():
            detail = f" ({result.detail})" if result.detail else ""
            click.echo(f"{project}: {result.status}{detail}")

    asyncio.run(_run())


async def _load_project_orgs(settings: "Settings") -> dict[str, str]:
    """Return {project_name: github_org} from the projects DB table.

    Best-effort: the org map is only an override on top of the "canonical"
    default (resolve_allowed_projects falls back to "canonical" for any
    project missing here), so a dev machine with no reachable DATABASE_URL
    must not block `mirrors sync` — the design has dev workers maintain their
    own mirrors, and all current projects are under github.com/canonical
    anyway. On any connection/query failure we log and return {}, letting the
    caller proceed with all-canonical resolution. The engine is always
    disposed so the command does not leak a connection pool on exit.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from craft_dashboard.models.project import Project

    engine = create_async_engine(settings.database_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(select(Project.name, Project.github_org))
            return dict(result.tuples().all())
    except (SQLAlchemyError, OSError) as exc:
        logging.getLogger(__name__).warning(
            "Could not load project orgs from DB (%s); "
            "falling back to the 'canonical' org for every project.",
            exc,
        )
        return {}
    finally:
        await engine.dispose()
