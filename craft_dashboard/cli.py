"""CLI entry point for craft-dashboard."""

import pathlib

import click
import uvicorn

from craft_dashboard.config import load_config


@click.group()
def main() -> None:
    """craft-dashboard: Dashboard and insights for *craft applications."""


@main.command()
@click.option("--host", default="127.0.0.1", help="Bind host.")
@click.option("--port", default=8000, type=int, help="Bind port.")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
def serve(*, host: str, port: int, reload: bool) -> None:
    """Start the web server."""
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
def collect(*, source: str, config_file: str) -> None:
    """Collect data from external sources."""
    config = load_config(pathlib.Path(config_file))
    click.echo(f"Collecting data from: {source}")
    click.echo(f"Projects: {len(config.craft_projects)}")
    # The actual async collection is handled by scripts/collect_data.py
    # This CLI command delegates to it
    click.echo("Use 'uv run scripts/collect_data.py' for cron-based collection.")
