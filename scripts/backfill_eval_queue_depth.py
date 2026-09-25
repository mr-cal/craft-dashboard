#!/usr/bin/env python3
"""Backfill historical eval_queue_snapshots pending_count values.

Historically, _maybe_record_queue_snapshot only counted open issues when
sampling queue depth, omitting closed issues awaiting evaluation. When prompt
eval versions were bumped (e.g. CURRENT_SUMMARY_VERSION to 5 on 2026-09-17 15:35),
all closed issues (~19k+) became pending but were not reflected in the snapshot
chart until they were evaluated down on 2026-09-24 and 2026-09-25.

This script replays closed-issue evaluation timelines and recalculates
pending_count across existing snapshots in eval_queue_snapshots.
"""

from __future__ import annotations

import argparse
import bisect
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent))

from craft_dashboard.config import load_config
from craft_dashboard.models.eval_queue_snapshot import EvalQueueSnapshot
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.settings import Settings

# Timestamp when CURRENT_SUMMARY_VERSION was bumped from 4 to 5 (commit 6019363b)
V5_BUMP_TIME = datetime(2026, 9, 17, 15, 35, 10, tzinfo=UTC)

_SAMPLE_DIFF_COUNT = 5


def _parse_utc_datetime(val: datetime | str | None) -> datetime | None:
    """Parse a datetime or ISO string and ensure UTC timezone."""
    if val is None:
        return None
    if isinstance(val, str):
        val = datetime.fromisoformat(val)
    if isinstance(val, datetime):
        return val.replace(tzinfo=UTC) if val.tzinfo is None else val.astimezone(UTC)
    return None


def _load_closed_issue_intervals(
    session: Session, filtered_issue_ids: set[tuple[str, str]]
) -> tuple[
    list[tuple[datetime, datetime | None]], list[tuple[datetime, datetime | None]]
]:
    """Load (closed_at, eval_at) intervals for closed issues under v4 and v5.

    Returns:
        (v4_intervals, v5_intervals): Lists of (closed_at, evaluated_at) tuples
        where evaluated_at is the earliest evaluation timestamp with the matching
        or higher version (or None if never evaluated).

    """
    # Fetch issues with their earliest v4 and v5 evaluation times
    stmt = (
        select(
            Issue.id,
            Issue.external_id,
            Project.name.label("project_name"),
            Issue.closed_at,
        )
        .join(Project, Issue.project_id == Project.id)
        .where(Issue.state != "open", Issue.closed_at.is_not(None))
    )
    issues = session.execute(stmt).all()

    # Query earliest eval_at for v4 and v5
    v4_earliest_q = text(
        """
        SELECT issue_id, min(evaluated_at) as first_v4
        FROM llm_evaluations
        WHERE eval_version >= 4
        GROUP BY issue_id
        """
    )
    v4_map = dict(session.execute(v4_earliest_q).all())

    v5_earliest_q = text(
        """
        SELECT issue_id, min(evaluated_at) as first_v5
        FROM llm_evaluations
        WHERE eval_version >= 5
        GROUP BY issue_id
        """
    )
    v5_map = dict(session.execute(v5_earliest_q).all())

    v4_intervals: list[tuple[datetime, datetime | None]] = []
    v5_intervals: list[tuple[datetime, datetime | None]] = []

    for issue_id, ext_id, proj_name, closed_at in issues:
        if (proj_name, str(ext_id)) in filtered_issue_ids:
            continue
        parsed_closed_at = _parse_utc_datetime(closed_at)
        if parsed_closed_at is None:
            continue

        first_v4 = _parse_utc_datetime(v4_map.get(issue_id))
        first_v5 = _parse_utc_datetime(v5_map.get(issue_id))

        v4_intervals.append((parsed_closed_at, first_v4))
        v5_intervals.append((parsed_closed_at, first_v5))

    return v4_intervals, v5_intervals


def backfill_snapshots(
    *,
    dry_run: bool = False,
    days: int | None = None,
) -> None:
    """Recalculate pending_count for snapshots."""
    settings = Settings()
    config = load_config(Path(settings.config_file))

    filtered_pairs = {
        (proj, str(num))
        for proj, nums in config.filtered_issues.items()
        for num in nums
    }

    sync_db_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    engine = create_engine(sync_db_url, echo=False)

    with Session(engine) as session:
        print("Loading closed issue timelines...")
        v4_intervals, v5_intervals = _load_closed_issue_intervals(
            session, filtered_pairs
        )
        print(f"Loaded {len(v5_intervals)} closed issues.")

        # Pre-sort closed_at and eval_at timestamps for O(log N) lookup
        v4_closed_ats = sorted([cl for cl, _ in v4_intervals])
        v4_eval_ats = sorted([ev for _, ev in v4_intervals if ev is not None])
        v5_closed_ats = sorted([cl for cl, _ in v5_intervals])
        v5_eval_ats = sorted([ev for _, ev in v5_intervals if ev is not None])

        # Query snapshots to update
        query = select(EvalQueueSnapshot).order_by(EvalQueueSnapshot.captured_at.asc())
        if days is not None:
            cutoff = datetime.now(tz=UTC) - timedelta(days=days)
            query = query.where(EvalQueueSnapshot.captured_at >= cutoff)

        snapshots = list(session.execute(query).scalars())
        print(f"Found {len(snapshots)} snapshots to process.")

        updated_count = 0
        sample_diffs: list[str] = []

        for i, snap in enumerate(snapshots):
            t = _parse_utc_datetime(snap.captured_at)
            if t is None:
                continue

            if t >= V5_BUMP_TIME:
                # Version 5 in effect
                closed_pending = bisect.bisect_right(
                    v5_closed_ats, t
                ) - bisect.bisect_right(v5_eval_ats, t)
            else:
                # Version 4 in effect
                closed_pending = bisect.bisect_right(
                    v4_closed_ats, t
                ) - bisect.bisect_right(v4_eval_ats, t)

            closed_pending = max(0, closed_pending)
            new_pending = snap.pending_count + closed_pending
            if new_pending != snap.pending_count:
                if len(sample_diffs) < _SAMPLE_DIFF_COUNT or i == len(snapshots) - 1:
                    sample_diffs.append(
                        f"  [{snap.captured_at.strftime('%Y-%m-%d %H:%M')}] ID {snap.id}: "
                        f"{snap.pending_count} -> {new_pending} (+{closed_pending} closed)"
                    )
                snap.pending_count = new_pending
                updated_count += 1

        print(f"Computed updates for {updated_count} snapshots.")
        print("Sample updates:")
        for diff in sample_diffs:
            print(diff)

        if dry_run:
            print("\n[DRY RUN] No changes committed.")
        else:
            session.commit()
            print(f"\n✓ Successfully committed {updated_count} snapshot updates.")


def main() -> None:
    """Run the snapshot queue depth backfill CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute updates without committing to database.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Limit backfill to snapshots within the last N days (default: all).",
    )
    args = parser.parse_args()
    backfill_snapshots(dry_run=args.dry_run, days=args.days)


if __name__ == "__main__":
    main()
