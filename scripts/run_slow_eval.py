#!/usr/bin/env python3
"""Run LLM evaluations at a slow, randomized, human-like pace.

Wraps ``scripts/run_llm.py evaluate`` by executing single-issue batches
(--limit 1 --concurrency 1) separated by a randomized delay (jitter).
This prevents high-volume automated bursts that could trigger abuse detection
on Copilot or external providers.

Usage:
    uv run scripts/run_slow_eval.py --count 10 --min-delay 20 --max-delay 50
    uv run scripts/run_slow_eval.py --count 5 --llm-backend local --project snapcraft
    uv run scripts/run_slow_eval.py --count 20 --min-delay 30 --max-delay 60 --all-issues
"""

from __future__ import annotations

import logging
import pathlib
import secrets
import subprocess
import sys
import time

import click

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("slow_eval")


def _interruptible_sleep(seconds: float) -> bool:
    """Sleep for *seconds* in small intervals, returning False if interrupted."""
    end_time = time.monotonic() + seconds
    while True:
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.5, remaining))


def build_command(
    run_llm_path: pathlib.Path,
    extra_args: list[str],
) -> list[str]:
    """Build the subprocess command for a single-issue evaluation."""
    cmd = [
        sys.executable,
        str(run_llm_path),
        "evaluate",
        "--concurrency",
        "1",
        "--limit",
        "1",
    ]
    cmd.extend(extra_args)
    return cmd


@click.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True}
)
@click.option(
    "--count",
    default=10,
    show_default=True,
    type=click.IntRange(min=0),
    help="Maximum number of issues to evaluate (0 for unlimited until stopped).",
)
@click.option(
    "--min-delay",
    default=25.0,
    show_default=True,
    type=click.FloatRange(min=0.0),
    help="Minimum randomized delay in seconds between evaluations.",
)
@click.option(
    "--max-delay",
    default=55.0,
    show_default=True,
    type=click.FloatRange(min=0.0),
    help="Maximum randomized delay in seconds between evaluations.",
)
@click.option(
    "--stop-on-error/--no-stop-on-error",
    default=True,
    show_default=True,
    help="Stop if an evaluation exits with an error code.",
)
@click.pass_context
def main(
    ctx: click.Context,
    count: int,
    min_delay: float,
    max_delay: float,
    stop_on_error: bool,
) -> None:
    """Run evaluations slowly with randomized inter-request delays."""
    if min_delay > max_delay:
        raise click.UsageError("--min-delay cannot be greater than --max-delay")

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    run_llm_path = repo_root / "scripts" / "run_llm.py"
    if not run_llm_path.is_file():
        raise click.UsageError(f"Could not find run_llm.py at {run_llm_path}")

    extra_args = list(ctx.args)
    logger.info(
        "Paced evaluation starting: target=%s issues, delay=%.1fs-%.1fs, extra_args=%s",
        count if count > 0 else "unlimited",
        min_delay,
        max_delay,
        extra_args or "[]",
    )

    completed = 0
    try:
        while True:
            if count > 0 and completed >= count:
                logger.info(
                    "Target of %d evaluation(s) reached. Exiting cleanly.", count
                )
                break

            current_num = completed + 1
            total_str = f"/{count}" if count > 0 else ""
            logger.info("=== Starting evaluation %d%s ===", current_num, total_str)

            cmd = build_command(run_llm_path, extra_args)
            result = subprocess.run(cmd, check=False)

            if result.returncode != 0:
                logger.error(
                    "Evaluation process exited with code %d.", result.returncode
                )
                if stop_on_error:
                    logger.warning(
                        "Stopping due to error (stop-on-error is enabled). Completed: %d",
                        completed,
                    )
                    sys.exit(result.returncode)

            completed += 1

            if count > 0 and completed >= count:
                logger.info("Target of %d evaluation(s) reached.", count)
                break

            sleep_duration = secrets.SystemRandom().uniform(min_delay, max_delay)
            logger.info(
                "Evaluation %d complete. Sleeping for %.1fs before next issue...",
                completed,
                sleep_duration,
            )
            interrupted = not _interruptible_sleep(sleep_duration)
            if interrupted:
                logger.info("Sleep interrupted. Exiting.")
                break

    except KeyboardInterrupt:
        logger.info(
            "Interrupted by user (Ctrl+C). Completed: %d evaluation(s).", completed
        )
        sys.exit(0)

    logger.info(
        "Paced evaluation finished. Total completed: %d evaluation(s).", completed
    )


if __name__ == "__main__":
    main()
