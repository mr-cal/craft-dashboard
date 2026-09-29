"""Tests for the in-memory evaluation worker liveness state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from craft_dashboard.services import eval_activity


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    eval_activity.reset()


class TestActivityRecording:
    def test_activity_starts_unknown(self) -> None:
        assert eval_activity.get_eval_activity() == (None, None)

    def test_records_next_and_result(self) -> None:
        eval_activity.record_next_call()
        eval_activity.record_result_submitted()
        last_next, last_result = eval_activity.get_eval_activity()
        assert last_next is not None
        assert last_result is not None


class TestQuotaPause:
    def test_future_pause_is_reported(self) -> None:
        resume_at = datetime.now(tz=UTC) + timedelta(minutes=5)
        eval_activity.set_quota_pause_until(resume_at)
        assert eval_activity.get_quota_pause_until() == resume_at

    def test_elapsed_pause_is_forgotten(self) -> None:
        """A stale report must not misreport a recovered worker as paused."""
        eval_activity.set_quota_pause_until(datetime.now(tz=UTC) - timedelta(hours=1))
        assert eval_activity.get_quota_pause_until() is None

    def test_spend_cap_pause_is_in_the_future(self) -> None:
        eval_activity.pause_for_spend_cap()
        paused_until = eval_activity.get_quota_pause_until()
        assert paused_until is not None
        assert paused_until > datetime.now(tz=UTC)


class TestQueueSnapshotThrottle:
    def test_first_call_records(self) -> None:
        assert eval_activity.should_record_queue_snapshot() is True

    def test_immediate_second_call_is_throttled(self) -> None:
        eval_activity.should_record_queue_snapshot()
        assert eval_activity.should_record_queue_snapshot() is False

    def test_records_again_once_the_interval_elapses(self) -> None:
        eval_activity.should_record_queue_snapshot()
        eval_activity.state.last_queue_snapshot_at = (
            datetime.now(tz=UTC)
            - eval_activity.QUEUE_SNAPSHOT_INTERVAL
            - timedelta(seconds=1)
        )
        assert eval_activity.should_record_queue_snapshot() is True
