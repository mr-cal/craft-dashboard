"""Unit tests for scripts/backfill_eval_queue_depth.py."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from craft_dashboard.models.base import Base
from craft_dashboard.models.eval_queue_snapshot import EvalQueueSnapshot
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from scripts.backfill_eval_queue_depth import V5_BUMP_TIME, backfill_snapshots
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def sync_db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_backfill_snapshots_recalculates_pending_count(sync_db_session, monkeypatch):
    """Snapshot pending_count is updated with pending closed issues."""
    project = Project(
        id=1, name="snapcraft", category="application", github_org="canonical"
    )
    sync_db_session.add(project)

    # Issue closed before bump, evaluated after bump
    closed_at = V5_BUMP_TIME - timedelta(days=5)
    issue1 = Issue(
        id=1,
        project_id=1,
        source="github",
        external_id="101",
        issue_type="issue",
        title="Bug 1",
        state="closed",
        closed_at=closed_at,
        last_fetched_at=V5_BUMP_TIME,
    )
    # v5 evaluation happens 2 days after bump
    eval1 = LLMEvaluation(
        id=1,
        issue_id=1,
        model_name="test-model",
        eval_version=5,
        evaluated_at=V5_BUMP_TIME + timedelta(days=2),
        latest=True,
    )
    sync_db_session.add_all([issue1, eval1])

    # Snapshot 1 day after bump: issue1 is closed and awaiting v5 eval -> pending!
    snap1 = EvalQueueSnapshot(
        id=1,
        captured_at=V5_BUMP_TIME + timedelta(days=1),
        pending_count=10,
        total_open=10,
        evaluated_today=5,
    )
    # Snapshot 3 days after bump: issue1 has been evaluated -> not pending!
    snap2 = EvalQueueSnapshot(
        id=2,
        captured_at=V5_BUMP_TIME + timedelta(days=3),
        pending_count=5,
        total_open=10,
        evaluated_today=20,
    )
    sync_db_session.add_all([snap1, snap2])
    sync_db_session.commit()

    with patch("scripts.backfill_eval_queue_depth.create_engine") as mock_engine:
        mock_engine.return_value = sync_db_session.bind
        with patch("scripts.backfill_eval_queue_depth.Settings") as mock_settings:
            mock_settings.return_value.database_url = "sqlite:///:memory:"
            mock_settings.return_value.config_file = "craft-dashboard.toml"
            with patch("scripts.backfill_eval_queue_depth.load_config") as mock_cfg:
                mock_cfg.return_value.filtered_issues = {}
                backfill_snapshots(dry_run=False)

    sync_db_session.refresh(snap1)
    sync_db_session.refresh(snap2)

    # snap1 should have 10 + 1 = 11
    assert snap1.pending_count == 11
    # snap2 should remain 5 + 0 = 5
    assert snap2.pending_count == 5
