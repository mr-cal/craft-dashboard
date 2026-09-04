"""Shared Rich console/progress-bar helpers for LLM evaluation CLIs.

Used by both ``scripts/llm/eval_worker.py`` (continuous HTTP evaluation)
and ``scripts/run_llm.py`` entrypoints so the evaluation worker keeps a
consistent interactive UX: colored logs, a live progress bar, and a
persistent-history-backed ETA.
"""

from __future__ import annotations

import logging
import pathlib
from datetime import datetime
from typing import TYPE_CHECKING

from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import Console
    from rich.progress import Task as RichTask
    from rich.progress import TaskID

    from scripts.eval_timing import TimingHistory


def setup_rich_logging(
    *,
    verbose: bool,
    console: Console,
    log: bool = False,
    log_dir: str | pathlib.Path = ".logs",
) -> pathlib.Path | None:
    """Install a RichHandler on the root logger for colored, readable logs.

    When ``log`` is True, also attach a FileHandler to write DEBUG-level logs
    (including full timestamps, logger names, and LLM debug traces) to a
    timestamped file in ``log_dir``.

    Explicitly replaces any existing handlers (rather than relying on
    ``logging.basicConfig()``'s no-op-if-already-configured behavior), so
    this is safe to call even in a process that already ran
    ``logging.basicConfig()`` earlier (e.g. a module import side effect).
    """
    rich_handler = RichHandler(
        console=console,
        show_path=False,
        show_time=verbose,
        markup=False,
        rich_tracebacks=False,
        log_time_format="%H:%M:%S",
    )
    rich_handler.setFormatter(logging.Formatter("%(message)s"))
    rich_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    class ConsoleFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if not verbose and record.name in ("httpx", "httpcore"):
                return record.levelno >= logging.WARNING
            return True

    rich_handler.addFilter(ConsoleFilter())

    handlers: list[logging.Handler] = [rich_handler]
    log_path: pathlib.Path | None = None

    if log:
        target_dir = pathlib.Path(log_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = target_dir / f"evaluate_{timestamp}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
        handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(logging.DEBUG if (verbose or log) else logging.INFO)
    if not verbose and not log:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    return log_path


def format_elapsed(seconds: float) -> str:
    """Format elapsed time as a human-readable string (e.g. '5s', '2m03s')."""
    if seconds < 60:  # noqa: PLR2004
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    return f"{minutes}m{remaining:02d}s"


class TimingEtaColumn(ProgressColumn):
    """ETA column backed by persistent :class:`TimingHistory`.

    Uses historical per-item durations to estimate completion time, so the
    ETA is available immediately from the first item (not just after Rich
    has accumulated enough in-run samples). When *concurrency* is greater
    than 1, the estimate divides remaining work by the worker count since
    items complete in parallel rather than strictly sequentially.
    """

    def __init__(
        self,
        history: TimingHistory,
        phase: str,
        concurrency: int = 1,
    ) -> None:
        """Initialize the column.

        Args:
            history: Persistent per-phase timing history used for ETA math.
            phase: Phase key to read/write in *history* (e.g. "evaluate").
            concurrency: Number of concurrent workers processing items;
                used to scale the ETA estimate down for parallel runs.

        """
        self._history = history
        self._phase = phase
        self._concurrency = max(1, concurrency)
        super().__init__()

    def render(self, task: RichTask) -> Text:
        """Render the ETA for the given Rich task."""
        if task.finished:
            return Text("—")
        if task.total is None:
            return Text("?")
        remaining = max(0, int(task.total - task.completed))
        effective_remaining = -(-remaining // self._concurrency)  # ceil division
        return Text(self._history.eta(self._phase, effective_remaining))


def make_progress(
    console: Console,
    total: int | None,  # noqa: ARG001
    timing: TimingHistory | None = None,
    phase: str | None = None,
    concurrency: int = 1,
) -> Progress:
    """Create a Rich Progress bar for an evaluation loop.

    Args:
        console: Rich console to render to (shared with logging output so
            log lines and the live bar interleave correctly).
        total: Unused directly here (kept for symmetry with callers that
            pass it alongside `add_task(total=...)`); retained for API
            stability.
        timing: Optional persistent timing history for ETA estimation. When
            omitted, falls back to Rich's built-in in-run ETA column.
        phase: Phase key to use with *timing* (required if *timing* is set).
        concurrency: Number of concurrent workers, used to scale the ETA.

    """
    eta_col: ProgressColumn = (
        TimingEtaColumn(timing, phase, concurrency)
        if timing is not None and phase is not None
        else TimeRemainingColumn()
    )
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        eta_col,
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


class ProgressTracker:
    """Advances a live progress bar (and records ETA timing) after each item.

    Wraps an optional Rich ``Progress``/``TimingHistory`` pair so callers can
    unconditionally call :meth:`finish` after every processed item without
    checking for ``None`` themselves. When no progress bar is active (e.g.
    ``console`` was never passed to the caller), every method is a no-op,
    which keeps the non-interactive code path exactly as cheap as before
    this class existed.
    """

    def __init__(
        self,
        *,
        progress: Progress | None,
        task_id: TaskID | None,
        timing: TimingHistory | None,
        phase: str,
    ) -> None:
        """Initialize the tracker.

        Args:
            progress: The live Rich Progress instance to advance, or None to
                disable progress reporting entirely.
            task_id: The task within *progress* to advance (ignored when
                *progress* is None).
            timing: Optional persistent timing history to record durations
                into, for ETA estimation on the current and future runs.
            phase: Phase key to use with *timing*.

        """
        self._progress = progress
        self._task_id = task_id
        self._timing = timing
        self._phase = phase

    def finish(self, outcome: str | None, elapsed: float) -> None:
        """Advance the bar by one item and record its duration for ETA.

        Args:
            outcome: One of "evaluated", "skipped", or "errored". Only
                "evaluated" durations are recorded into the timing history,
                since skipped/errored items don't reflect a full LLM call's
                typical duration and would skew the ETA.
            elapsed: Wall-clock seconds spent processing the item.

        """
        if self._progress is not None:
            self._progress.update(self._task_id, advance=1)
        if self._timing is not None and outcome == "evaluated":
            self._timing.add(self._phase, elapsed)
