#!/usr/bin/env python3
"""One-time migration of CSV/JSON data from starcraft-stats to PostgreSQL.

Usage:
    uv run scripts/migrate_csv.py --data-dir /path/to/starcraft-stats/html/data

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
"""

import asyncio
import csv
import json
import logging
import pathlib
import sys
from datetime import datetime, timezone

import click

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _migrate_projects(session, config) -> dict[str, int]:
    """Create project records and return name-to-id mapping."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.project import Project

    name_to_id = {}

    for i, name in enumerate(config.craft_projects):
        category = "application" if name in config.craft_applications else (
            "library" if name in config.craft_libraries else "other"
        )
        lp_name = name if name in config.launchpad_projects else None

        stmt = insert(Project).values(
            name=name,
            category=category,
            launchpad_name=lp_name,
            display_order=i,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
        await session.execute(stmt)

    await session.commit()

    result = await session.execute(select(Project.id, Project.name))
    for row in result:
        name_to_id[row.name] = row.id

    return name_to_id


async def _migrate_snapshots(
    session,
    data_dir: pathlib.Path,
    name_to_id: dict[str, int],
) -> int:
    """Migrate snapshot data from per-project CSV files."""
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.snapshot import Snapshot

    count = 0

    for project_name, project_id in name_to_id.items():
        csv_path = data_dir / f"{project_name}-github.csv"
        if not csv_path.exists():
            continue

        with csv_path.open() as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    snapshot_date = datetime.strptime(
                        row.get("date", row.get("Date", "")), "%Y-%m-%d"
                    ).date()
                except (ValueError, KeyError):
                    continue

                stmt = insert(Snapshot).values(
                    project_id=project_id,
                    snapshot_date=snapshot_date,
                    open_issues=int(row.get("open_issues", 0)),
                    open_prs=int(row.get("open_prs", 0)),
                    open_issues_external=int(row.get("open_issues_ext", 0)),
                    open_issues_internal=int(row.get("open_issues_int", 0)),
                    open_prs_external=int(row.get("open_prs_ext", 0)),
                    open_prs_internal=int(row.get("open_prs_int", 0)),
                    open_bugs=int(row.get("open_bugs", 0)),
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["project_id", "snapshot_date"]
                )
                await session.execute(stmt)
                count += 1

    await session.commit()
    logger.info("Migrated %d snapshot rows", count)
    return count


async def _main(data_dir: pathlib.Path) -> None:
    """Run the migration."""
    settings = Settings()
    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    try:
        async with session_factory() as session:
            logger.info("Migrating projects...")
            name_to_id = await _migrate_projects(session, config)
            logger.info("Created %d projects", len(name_to_id))

            logger.info("Migrating snapshots...")
            await _migrate_snapshots(session, data_dir, name_to_id)

        logger.info("Migration complete!")
    finally:
        await engine.dispose()


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(exists=True, path_type=pathlib.Path),
    required=True,
    help="Path to the starcraft-stats html/data directory.",
)
def main(data_dir: pathlib.Path) -> None:
    """Migrate CSV/JSON data from starcraft-stats to PostgreSQL."""
    asyncio.run(_main(data_dir))


if __name__ == "__main__":
    main()
