"""Shared process state and runtime wiring for the evaluation worker.

Holds the process-wide pause/shutdown flags, the per-run counters, the
dependency bundle passed to every worker coroutine, and the TTY/signal
plumbing that drives them. No HTTP or LLM calls live here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import select
import sys
import termios
import threading
import time
import tty
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path

    import httpx
    from craft_dashboard.llm.client import LLMClient
    from craft_dashboard.llm.evaluator import IssueEvaluator
    from rich.progress import Progress, TaskID

    from scripts.eval_timing import TimingHistory

logger = logging.getLogger(__name__)

shutdown_state = {"requested": False}
paused_state = {"paused": False}


async def timed[T](coro: Awaitable[T]) -> tuple[T, float]:
    """Run an awaitable and return ``(result, elapsed_seconds)``."""
    started_at = time.monotonic()
    result = await coro
    return result, time.monotonic() - started_at


class RunState:
    """Shared counters and limit reservation state for worker coroutines."""

    def __init__(self, *, limit: int) -> None:
        self.limit = limit
        self.reserved = 0
        self.evaluated = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.consecutive_failures = 0
        self.lock = asyncio.Lock()

    async def reserve(self) -> bool:
        """Reserve one evaluation slot when a finite limit is active."""
        if self.limit <= 0:
            return True
        async with self.lock:
            if self.reserved >= self.limit:
                return False
            self.reserved += 1
            return True

    async def release(self) -> None:
        """Release a reserved slot after a failed or skipped attempt."""
        if self.limit <= 0:
            return
        async with self.lock:
            self.reserved = max(0, self.reserved - 1)

    async def record_failure(self) -> int:
        """Record one failed evaluation and return current consecutive failures."""
        async with self.lock:
            self.consecutive_failures += 1
            return self.consecutive_failures

    async def complete(self, *, prompt_tokens: int, completion_tokens: int) -> int:
        """Record one successful evaluation and return the new total."""
        async with self.lock:
            self.evaluated += 1
            self.consecutive_failures = 0
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            return self.evaluated


class Runtime:
    """Shared runtime dependencies for worker coroutines."""

    def __init__(
        self,
        *,
        client: LLMClient,
        evaluator: IssueEvaluator,
        http_client: httpx.AsyncClient,
        headers: dict[str, str],
        params: dict[str, Any],
        progress: Progress,
        overall_id: TaskID,
        timing: TimingHistory,
        state: RunState,
        poll_interval: int,
        issue_limit: int,
        model: str,
        llm_backend: str,
        mirror_dir: Path,
        allowed_projects: dict[str, str],
        eval_server_base_url: str,
        single_issue: bool = False,
        slow_eval: bool = False,
        min_delay: float = 25.0,
        max_delay: float = 55.0,
    ) -> None:
        self.client = client
        self.evaluator = evaluator
        self.http_client = http_client
        self.headers = headers
        self.params = params
        self.progress = progress
        self.overall_id = overall_id
        self.timing = timing
        self.state = state
        self.poll_interval = poll_interval
        self.issue_limit = issue_limit
        self.model = model
        self.llm_backend = llm_backend
        self.mirror_dir = mirror_dir
        self.allowed_projects = allowed_projects
        self.eval_server_base_url = eval_server_base_url
        self.slow_eval = slow_eval
        self.min_delay = min_delay
        self.max_delay = max_delay
        #: True for a single-target ``--issue ... --force`` run (e.g. the
        #: canary script). In that mode there is only ever one issue to
        #: evaluate, and a claimed-then-discarded/failed/skipped issue is
        #: never re-offered by ``/next`` (the server sees it as already
        #: attempted), so any terminal outcome — not just success — must
        #: end the run. Otherwise the worker polls "no work available"
        #: forever, bounded only by an external timeout.
        self.single_issue = single_issue


async def release_and_maybe_stop(runtime: Runtime) -> None:
    """Release a reserved slot after a terminal (non-retryable) outcome.

    In single-issue mode (see ``Runtime.single_issue``) this also requests
    shutdown, since there is nothing else left to poll for: a
    claimed-then-discarded/failed/skipped issue is never re-offered by
    ``/next`` in a single-issue ``--force`` run, so without this the worker
    would poll "no work available" forever, bounded only by an external
    timeout.
    """
    await runtime.state.release()
    if runtime.single_issue:
        shutdown_state["requested"] = True


def signal_handler(signum: int, frame: object) -> None:
    """Request a graceful shutdown, or exit immediately on a second signal."""
    del signum, frame
    if shutdown_state["requested"]:
        # Second Ctrl+C: the user already asked us to wind down gracefully
        # and doesn't want to wait (e.g. a stuck in-flight LLM call). Exit
        # immediately rather than waiting for the current evaluation.
        logger.info("Shutting down immediately...")
        os._exit(1)
    shutdown_state["requested"] = True
    logger.info("Shutting down after current evaluation...")


def start_keyboard_monitor() -> None:
    """Monitor stdin for space key to pause/unpause. No-op if not a TTY."""
    if not sys.stdin.isatty():
        return

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    def _listen() -> None:
        with contextlib.suppress(Exception):
            tty.setcbreak(fd)
            while not shutdown_state["requested"]:
                ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch == " ":
                        paused_state["paused"] = not paused_state["paused"]
                        if paused_state["paused"]:
                            logger.info("Paused — press space to resume")
                        else:
                            logger.info("Resuming")
        with contextlib.suppress(Exception):
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    threading.Thread(target=_listen, daemon=True).start()
