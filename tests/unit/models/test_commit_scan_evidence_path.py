"""Unit tests for the CommitScanEvidencePath model."""

from __future__ import annotations

from craft_dashboard.models.commit_scan_evidence_path import CommitScanEvidencePath

from tests.factories import make_issue, make_project


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


class TestCommitScanEvidencePath:
    """Tests for the (project, path) -> issue_id reverse index."""

    async def test_create_evidence_path(self, test_db_session) -> None:
        project = make_project(id=1, name="craft-parts")
        issue = make_issue(id=1, project_id=1, external_id="1")
        await _seed(test_db_session, project, issue)

        entry = CommitScanEvidencePath(
            issue_id=1,
            project="craft-parts",
            path="craft_parts/executor/step_handler.py",
        )
        test_db_session.add(entry)
        await test_db_session.commit()

        assert entry.id is not None
