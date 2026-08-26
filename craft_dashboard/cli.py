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


@main.group(name="commit-scanner")
def commit_scanner_group() -> None:
    """Scan new commits for issues they might invalidate."""


@commit_scanner_group.command(name="run")
@click.option(
    "--config-file",
    type=click.Path(exists=True),
    default="craft-dashboard.toml",
    help="Path to configuration file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Report what would be invalidated without writing anything.",
)
@click.option(
    "--top-k",
    type=int,
    default=None,
    help="Semantic-match K (candidates per commit). Overrides the configured default.",
)
@click.option(
    "--threshold",
    type=float,
    default=None,
    help=(
        "Semantic cosine-similarity threshold (0-1). Overrides the configured default."
    ),
)
def commit_scanner_run(
    *, config_file: str, dry_run: bool, top_k: int | None, threshold: float | None
) -> None:
    """Run one commit-scanner pass over every tracked project."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from craft_dashboard.commit_scanner.scanner import scan_all_projects
    from craft_dashboard.config import load_config
    from craft_dashboard.git_mirrors import reader
    from craft_dashboard.git_mirrors.paths import resolve_allowed_projects
    from craft_dashboard.llm.client import OPENROUTER_BASE_URL
    from craft_dashboard.llm.embeddings import EmbeddingClient
    from craft_dashboard.settings import Settings

    async def _run() -> None:
        settings = Settings()
        reader.set_git_concurrency(settings.git_concurrency)
        config = load_config(pathlib.Path(config_file))
        project_orgs = await _load_project_orgs(settings)
        allowed_projects = resolve_allowed_projects(
            craft_projects=config.craft_projects,
            project_orgs=project_orgs,
        )

        engine = create_async_engine(settings.database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        embed_client = (
            EmbeddingClient(
                base_url=OPENROUTER_BASE_URL,
                model=settings.semantic_search_embedding_model,
                api_key=settings.openrouter_api_key,
                ca_cert="",
            )
            if settings.openrouter_api_key
            else None
        )
        try:
            async with session_factory() as session:
                summaries = await scan_all_projects(
                    session,
                    mirror_dir=settings.mirror_dir_path,
                    allowed_projects=allowed_projects,
                    embed_client=embed_client,
                    launchpad_projects=set(config.launchpad_projects),
                    semantic_top_k=(
                        top_k if top_k is not None else settings.commit_scanner_top_k
                    ),
                    semantic_similarity_threshold=(
                        threshold
                        if threshold is not None
                        else settings.commit_scanner_similarity_threshold
                    ),
                    dry_run=dry_run,
                )
        finally:
            if embed_client is not None:
                await embed_client.close()
            await engine.dispose()

        for summary in summaries:
            click.echo(
                f"{summary['project']}: {summary['commits_scanned']} commits, "
                f"invalidated qualified={summary['qualified_ref']} "
                f"path={summary['path']} semantic={summary['semantic']} "
                f"bare={summary['bare_ref']} "
                f"launchpad={summary['launchpad']}" + (" [dry-run]" if dry_run else "")
            )

    asyncio.run(_run())
