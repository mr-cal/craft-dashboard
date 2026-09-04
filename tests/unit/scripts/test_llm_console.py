"""Tests for scripts.llm.console (shared Rich progress/logging helpers)."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import Progress, TimeRemainingColumn
from scripts.eval_timing import PHASE_EVALUATE, TimingHistory
from scripts.llm.console import (
    TimingEtaColumn,
    format_elapsed,
    make_progress,
    setup_rich_logging,
)

if TYPE_CHECKING:
    import pathlib


class TestFormatElapsed:
    """Tests for format_elapsed()."""

    def test_formats_seconds_under_a_minute(self) -> None:
        assert format_elapsed(5.4) == "5s"

    def test_formats_zero_seconds(self) -> None:
        assert format_elapsed(0) == "0s"

    def test_formats_minutes_and_seconds(self) -> None:
        assert format_elapsed(125) == "2m05s"

    def test_formats_exact_minute_boundary(self) -> None:
        assert format_elapsed(60) == "1m00s"


class TestSetupRichLogging:
    """Tests for setup_rich_logging()."""

    def test_replaces_existing_handlers(self) -> None:
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        try:
            root.handlers = [logging.NullHandler(), logging.NullHandler()]
            console = Console(file=io.StringIO())
            setup_rich_logging(verbose=False, console=console)
            assert len(root.handlers) == 1
        finally:
            root.handlers = original_handlers

    def test_sets_debug_level_when_verbose(self) -> None:
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        try:
            console = Console(file=io.StringIO())
            setup_rich_logging(verbose=True, console=console)
            assert root.level == logging.DEBUG
        finally:
            root.handlers = original_handlers
            root.setLevel(original_level)

    def test_sets_info_level_when_not_verbose(self) -> None:
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        try:
            console = Console(file=io.StringIO())
            setup_rich_logging(verbose=False, console=console)
            assert root.level == logging.INFO
        finally:
            root.handlers = original_handlers
            root.setLevel(original_level)

    def test_quiets_noisy_loggers_when_not_verbose(self) -> None:
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        try:
            console = Console(file=io.StringIO())
            setup_rich_logging(verbose=False, console=console)
            assert logging.getLogger("httpx").level == logging.WARNING
            assert logging.getLogger("httpcore").level == logging.WARNING
        finally:
            root.handlers = original_handlers

    def test_creates_log_file_when_log_is_true(self, tmp_path: pathlib.Path) -> None:
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        try:
            console = Console(file=io.StringIO())
            log_path = setup_rich_logging(
                verbose=False, console=console, log=True, log_dir=tmp_path
            )
            assert log_path is not None
            assert log_path.exists()
            assert len(root.handlers) == 2
            assert root.level == logging.DEBUG

            test_logger = logging.getLogger("craft_dashboard.test")
            test_logger.debug("test debug message in log")
            for handler in root.handlers:
                handler.flush()

            content = log_path.read_text(encoding="utf-8")
            assert "test debug message in log" in content
        finally:
            for handler in root.handlers:
                handler.close()
            root.handlers = original_handlers
            root.setLevel(original_level)


class TestTimingEtaColumn:
    """Tests for TimingEtaColumn."""

    def _make_progress_with_task(self, console: Console) -> tuple[Progress, int]:
        progress = Progress(TimeRemainingColumn(), console=console)
        task_id = progress.add_task("test", total=10)
        return progress, task_id

    def test_renders_dash_when_task_finished(self, tmp_path: pathlib.Path) -> None:
        history = TimingHistory(path=tmp_path / "timing.json")
        console = Console(file=io.StringIO())
        progress, task_id = self._make_progress_with_task(console)
        column = TimingEtaColumn(history, PHASE_EVALUATE)
        progress.update(task_id, completed=10)
        task = progress.tasks[0]
        assert str(column.render(task)) == "—"

    def test_renders_question_mark_when_total_unknown(
        self, tmp_path: pathlib.Path
    ) -> None:
        history = TimingHistory(path=tmp_path / "timing.json")
        console = Console(file=io.StringIO())
        progress = Progress(console=console)
        task_id = progress.add_task("test", total=None)
        column = TimingEtaColumn(history, PHASE_EVALUATE)
        task = next(t for t in progress.tasks if t.id == task_id)
        assert str(column.render(task)) == "?"

    def test_scales_remaining_by_concurrency(self, tmp_path: pathlib.Path) -> None:
        history = TimingHistory(path=tmp_path / "timing.json")
        history.add(PHASE_EVALUATE, 10.0)
        console = Console(file=io.StringIO())
        progress, task_id = self._make_progress_with_task(console)
        progress.update(task_id, completed=0)
        task = progress.tasks[0]

        sequential = TimingEtaColumn(history, PHASE_EVALUATE, concurrency=1)
        parallel = TimingEtaColumn(history, PHASE_EVALUATE, concurrency=5)

        # 10 remaining items at ~10s each sequentially vs. divided across
        # 5 concurrent workers (ceil(10/5)=2 effective remaining) should
        # produce a much shorter parallel ETA string.
        seq_eta = str(sequential.render(task))
        par_eta = str(parallel.render(task))
        assert seq_eta != par_eta


class TestMakeProgress:
    """Tests for make_progress()."""

    def test_creates_progress_with_timing_eta_column(
        self, tmp_path: pathlib.Path
    ) -> None:
        console = Console(file=io.StringIO())
        history = TimingHistory(path=tmp_path / "timing.json")
        progress = make_progress(console, 10, history, PHASE_EVALUATE)
        assert any(isinstance(col, TimingEtaColumn) for col in progress.columns)

    def test_falls_back_to_time_remaining_column_without_timing(self) -> None:
        console = Console(file=io.StringIO())
        progress = make_progress(console, 10)
        assert any(isinstance(col, TimeRemainingColumn) for col in progress.columns)
        assert not any(isinstance(col, TimingEtaColumn) for col in progress.columns)

    def test_add_task_works_before_progress_started(self) -> None:
        console = Console(file=io.StringIO())
        progress = make_progress(console, 10)
        task_id = progress.add_task("Evaluating issues", total=10, completed=0)
        assert progress.tasks[0].id == task_id
