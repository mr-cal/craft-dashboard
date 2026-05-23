"""Integration tests for snapshot retention."""

from datetime import date, timedelta

from craft_dashboard.models.project import Project
from craft_dashboard.models.snapshot import Snapshot
from craft_dashboard.utils.retention import prune_old_snapshots
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def test_prune_old_snapshots_removes_only_expired_rows(
    test_db_session: AsyncSession,
) -> None:
    """Snapshot pruning should keep rows at or newer than the retention cutoff."""
    today = date.today()
    project = Project(
        name="rockcraft",
        category="application",
        github_org="canonical",
    )
    test_db_session.add(project)
    await test_db_session.flush()

    snapshot_dates = [
        today - timedelta(days=400),
        today - timedelta(days=366),
        today - timedelta(days=365),
        today,
    ]
    test_db_session.add_all(
        [
            Snapshot(project_id=project.id, snapshot_date=snapshot_date)
            for snapshot_date in snapshot_dates
        ]
    )
    await test_db_session.commit()

    deleted = await prune_old_snapshots(test_db_session, retention_days=365)

    remaining_dates = list(
        await test_db_session.scalars(
            select(Snapshot.snapshot_date).order_by(Snapshot.snapshot_date)
        )
    )

    assert deleted == 2
    assert remaining_dates == [today - timedelta(days=365), today]
