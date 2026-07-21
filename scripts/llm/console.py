"""Shared Rich console/progress-bar helpers for LLM evaluation CLIs.

Used by both ``scripts/eval_client.py`` (pull-based local evaluation) and
``scripts/llm/cli.py`` (server-side batch evaluation via OpenRouter) so both
tools present a consistent interactive UX: colored logs, a live progress bar,
and a persistent-history-backed ETA.
"""

from __future__ import annotations

import logging
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

    from scripts.eval_timing import TimingHistory


def setup_rich_logging(*, verbose: bool, console: Console) -> None:
    """Install a RichHandler on the root logger for colored, readable logs.

    Explicitly replaces any existing handlers (rather than relying on
    ``logging.basicConfig()``'s no-op-if-already-configured behavior), so
    this is safe to call even in a process that already ran
    ``logging.basicConfig()`` earlier (e.g. a module import side effect).
    """
    handler = RichHandler(
        console=console,
        show_path=False,
        show_time=verbose,
        markup=False,
        rich_tracebacks=False,
        log_time_format="%H:%M:%S",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("craft_dashboard.llm.client").setLevel(logging.WARNING)


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
