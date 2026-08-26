"""Unit tests for the CommitScanRun model."""

from __future__ import annotations

from datetime import UTC, datetime

from craft_dashboard.models.commit_scan_run import CommitScanRun

from tests.factories import make_project


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


class TestCommitScanRun:
    """Tests for CommitScanRun, the per-pass observability record."""

    async def test_create_run_with_signal_breakdown(self, test_db_session) -> None:
        project = make_project(id=1, name="craft-parts")
        await _seed(test_db_session, project)

        run = CommitScanRun(
            project_id=1,
            scanned_at=datetime.now(tz=UTC),
            commits_scanned=12,
            sha_before="a" * 40,
            sha_after="b" * 40,
            duration_seconds=4.2,
            invalidated_qualified_ref=3,
            invalidated_path=5,
            invalidated_semantic=2,
            invalidated_bare_ref=1,
            invalidated_launchpad=4,
            dry_run=False,
        )
        test_db_session.add(run)
        await test_db_session.commit()

        assert run.id is not None
        assert run.invalidated_path == 5
        assert run.invalidated_launchpad == 4

    async def test_dry_run_flag_defaults_false(self, test_db_session) -> None:
        project = make_project(id=1, name="craft-parts")
        await _seed(test_db_session, project)

        run = CommitScanRun(
            project_id=1,
            scanned_at=datetime.now(tz=UTC),
            commits_scanned=0,
            sha_before="a" * 40,
            sha_after="a" * 40,
            duration_seconds=0.1,
        )
        test_db_session.add(run)
        await test_db_session.commit()

        assert run.dry_run is False
        assert run.invalidated_qualified_ref == 0
        assert run.invalidated_launchpad == 0
