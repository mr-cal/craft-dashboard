"""Tests for scripts.eval_timing."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.eval_timing import MAX_WINDOW, PHASE_DETECT, PHASE_EVALUATE, TimingHistory

if TYPE_CHECKING:
    import pathlib


class TestTimingHistoryPersistence:
    """Tests for load / save behaviour."""

    def test_creates_file_on_first_add(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "timing.json"
        th = TimingHistory(path)
        assert not path.exists()
        th.add(PHASE_EVALUATE, 10.0)
        assert path.exists()

    def test_creates_parent_directories(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "nested" / "deeply" / "timing.json"
        th = TimingHistory(path)
        th.add(PHASE_EVALUATE, 5.0)
        assert path.exists()

    def test_loads_existing_data(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "timing.json"
        path.write_text(json.dumps({"phase1": [1.0, 2.0, 3.0]}))
        th = TimingHistory(path)
        assert th.sample_count(PHASE_EVALUATE) == 3

    def test_corrupted_file_starts_empty(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "timing.json"
        path.write_text("not valid json {{{{")
        th = TimingHistory(path)
        assert th.sample_count(PHASE_EVALUATE) == 0

    def test_wrong_type_in_file_starts_empty(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "timing.json"
        path.write_text(json.dumps([1, 2, 3]))  # list, not dict
        th = TimingHistory(path)
        assert th.sample_count(PHASE_EVALUATE) == 0

    def test_non_list_values_are_skipped(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "timing.json"
        path.write_text(json.dumps({"phase1": [1.0, 2.0], "phase2": "bad"}))
        th = TimingHistory(path)
        assert th.sample_count(PHASE_EVALUATE) == 2
        assert th.sample_count(PHASE_DETECT) == 0

    def test_data_survives_reload(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "timing.json"
        th = TimingHistory(path)
        th.add(PHASE_EVALUATE, 12.5)
        th.add(PHASE_EVALUATE, 8.0)

        th2 = TimingHistory(path)
        assert th2.sample_count(PHASE_EVALUATE) == 2
        avg = th2.avg_seconds(PHASE_EVALUATE)
        assert avg is not None
        assert abs(avg - 10.25) < 0.01

    def test_missing_file_starts_empty(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "nonexistent.json"
        th = TimingHistory(path)
        assert th.sample_count(PHASE_EVALUATE) == 0


class TestTimingHistoryRollingWindow:
    """Tests for the rolling MAX_WINDOW limit."""

    def test_window_does_not_exceed_max(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        for i in range(MAX_WINDOW + 50):
            th.add(PHASE_EVALUATE, float(i))
        assert th.sample_count(PHASE_EVALUATE) == MAX_WINDOW

    def test_window_keeps_most_recent_samples(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        # Add old samples (value 1.0) then newer ones (value 99.0)
        for _ in range(MAX_WINDOW):
            th.add(PHASE_EVALUATE, 1.0)
        for _ in range(10):
            th.add(PHASE_EVALUATE, 99.0)
        # Window should now contain 90 x 1.0 + 10 x 99.0
        avg = th.avg_seconds(PHASE_EVALUATE)
        assert avg is not None
        expected = (90 * 1.0 + 10 * 99.0) / 100
        assert abs(avg - expected) < 0.01

    def test_phases_are_independent(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        for _ in range(5):
            th.add(PHASE_EVALUATE, 10.0)
        for _ in range(3):
            th.add(PHASE_DETECT, 3.0)
        assert th.sample_count(PHASE_EVALUATE) == 5
        assert th.sample_count(PHASE_DETECT) == 3

    def test_clear_removes_phase_data(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 10.0)
        th.clear(PHASE_EVALUATE)
        assert th.sample_count(PHASE_EVALUATE) == 0

    def test_clear_unknown_phase_is_noop(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.clear("nonexistent")  # should not raise


class TestTimingHistoryAvg:
    """Tests for avg_seconds()."""

    def test_avg_returns_none_with_no_data(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        assert th.avg_seconds(PHASE_EVALUATE) is None

    def test_avg_single_sample(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 42.0)
        assert th.avg_seconds(PHASE_EVALUATE) == pytest.approx(42.0)

    def test_avg_multiple_samples(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        for v in [10.0, 20.0, 30.0]:
            th.add(PHASE_EVALUATE, v)
        assert th.avg_seconds(PHASE_EVALUATE) == pytest.approx(20.0)


class TestTimingHistoryEta:
    """Tests for eta() formatting."""

    def test_eta_no_data_returns_question_mark(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        assert th.eta(PHASE_EVALUATE, 10) == "?"

    def test_eta_zero_remaining_returns_dash(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 10.0)
        assert th.eta(PHASE_EVALUATE, 0) == "\u2014"

    def test_eta_negative_remaining_returns_dash(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 10.0)
        assert th.eta(PHASE_EVALUATE, -5) == "\u2014"

    def test_eta_seconds_format(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 5.0)  # avg = 5s
        result = th.eta(PHASE_EVALUATE, 1)  # 1 remaining x 5s = 5s
        assert result == "5s"

    def test_eta_minutes_format(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 60.0)  # avg = 60s
        result = th.eta(PHASE_EVALUATE, 5)  # 5 x 60s = 300s = 5m 0s
        assert result == "5m 0s"

    def test_eta_hours_format(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 3600.0)  # avg = 1h
        result = th.eta(PHASE_EVALUATE, 2)  # 2 x 3600s = 2h
        assert result == "2h 0m"

    def test_eta_days_format(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 86400.0)  # avg = 24h
        result = th.eta(PHASE_EVALUATE, 3)  # 3 x 86400 = 3 days
        assert result == "3d 0h"

    def test_eta_mixed_minutes_seconds(self, tmp_path: pathlib.Path) -> None:
        th = TimingHistory(tmp_path / "t.json")
        th.add(PHASE_EVALUATE, 90.0)  # avg = 90s
        result = th.eta(PHASE_EVALUATE, 1)  # 1 x 90s = 1m 30s
        assert result == "1m 30s"
