"""CLI entry point for craft-dashboard."""

import click
import uvicorn


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
