"""Canary rollout tool for the deep-evaluation Phase 6 rewrite.

Evaluates an explicit, small set of issues one at a time via the real
`/api/eval/next` -> evaluate -> `/api/eval/result` HTTP pipeline
(`scripts.llm.eval_worker.run_evaluate_loop`), the exact same code path the
continuous production worker uses. Each target is evaluated with
`--force`/`--issue` semantics (bypassing version/hash eligibility, so this
never depends on or triggers a `CURRENT_EVAL_VERSION` bump) and a hard
per-issue timeout, so a hang or bug affects at most one issue instead of the
whole backlog.

Use this to review a handful of real evaluations on production before
letting the continuous worker process the full backlog (e.g. before/after a
`CURRENT_EVAL_VERSION` bump). See docs/evaluate.md's "Canary rollout"
section for the full staged-rollout procedure.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import click

from scripts.llm.eval_worker import run_evaluate_loop


@dataclass(frozen=True)
class CanaryTarget:
    """One issue/PR to canary-evaluate, identified by project and number."""

    project: str
    issue: str


def parse_targets(raw: tuple[str, ...]) -> list[CanaryTarget]:
    """Parse `PROJECT:NUMBER` strings into `CanaryTarget`s.

    Raises:
        click.UsageError: if any entry is missing the `:` separator or either
            half is empty.

    """
    targets = []
    for item in raw:
        project, sep, issue = item.partition(":")
        if not sep or not project or not issue:
            raise click.UsageError(
                f"Invalid --issue value {item!r}; expected PROJECT:NUMBER"
            )
        targets.append(CanaryTarget(project=project, issue=issue))
    return targets


async def _run_one(
    target: CanaryTarget,
    *,
    server: str,
    token: str,
    model_summary: str,
    model_scoring: str,
    openrouter_api_key: str,
    llm_backend: str,
    timeout_seconds: int,
) -> str:
    """Evaluate a single canary target with a hard timeout.

    Returns a short human-readable status string ("ok", "timeout", or
    "error: <message>") rather than raising, so the caller can keep a
    per-target result log without a failed target crashing the whole batch
    loop unexpectedly.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            await run_evaluate_loop(
                server=server,
                token=token,
                model_summary=model_summary,
                model_scoring=model_scoring,
                llm_backend=llm_backend,
                llm_url="",
                llm_api_key="",
                ca_cert="",
                poll_interval=5,
                limit=1,
                project=target.project,
                open_only=True,
                force=True,
                incomplete=False,
                stale_days=0,
                server_ca_cert="",
                verbose=True,
                openrouter_api_key=openrouter_api_key,
                issue=target.issue,
                concurrency=1,
            )
    except TimeoutError:
        return "timeout"
    except Exception as exc:
        return f"error: {exc}"
    return "ok"


@click.command()
@click.option(
    "--server",
    required=True,
    envvar="EVAL_CLIENT_SERVER",
    help="Base URL of craft-dashboard server [env: EVAL_CLIENT_SERVER]",
)
@click.option(
    "--token",
    required=True,
    envvar="EVAL_API_TOKEN",
    help="Eval API bearer token [env: EVAL_API_TOKEN]",
)
@click.option(
    "--issue",
    "issues",
    multiple=True,
    required=True,
    help="PROJECT:NUMBER to canary-evaluate. Repeatable.",
)
@click.option(
    "--model-summary",
    required=True,
    envvar="OPENROUTER_MODEL_SUMMARY",
    help="[env: OPENROUTER_MODEL_SUMMARY]",
)
@click.option(
    "--model-scoring",
    required=True,
    envvar="OPENROUTER_MODEL_SCORING",
    help="[env: OPENROUTER_MODEL_SCORING]",
)
@click.option(
    "--openrouter-api-key",
    required=True,
    envvar="OPENROUTER_API_KEY",
    help="[env: OPENROUTER_API_KEY]",
)
@click.option(
    "--llm-backend",
    type=click.Choice(["openrouter", "local"], case_sensitive=False),
    default="openrouter",
    show_default=True,
)
@click.option(
    "--timeout-seconds",
    default=300,
    show_default=True,
    type=click.IntRange(min=1),
    help="Hard per-issue timeout; a hung evaluation stops the batch instead of hanging forever.",
)
def cli(
    server: str,
    token: str,
    issues: tuple[str, ...],
    model_summary: str,
    model_scoring: str,
    openrouter_api_key: str,
    llm_backend: str,
    timeout_seconds: int,
) -> None:
    """Evaluate an explicit, small set of issues one at a time for canary review.

    Stops after the first non-"ok" result (timeout or error) rather than
    continuing through the rest of the batch, so a bug surfaces immediately
    with a small, reviewable blast radius instead of silently degrading
    through the remaining targets.
    """
    targets = parse_targets(issues)
    results: list[tuple[CanaryTarget, str]] = []
    for target in targets:
        click.echo(f"--- Evaluating {target.project}#{target.issue} ---")
        status = asyncio.run(
            _run_one(
                target,
                server=server,
                token=token,
                model_summary=model_summary,
                model_scoring=model_scoring,
                openrouter_api_key=openrouter_api_key,
                llm_backend=llm_backend,
                timeout_seconds=timeout_seconds,
            )
        )
        results.append((target, status))
        if status != "ok":
            click.echo(
                f"{target.project}#{target.issue}: {status} — stopping batch",
                err=True,
            )
            break

    click.echo("\n=== Canary summary ===")
    for target, status in results:
        click.echo(f"{target.project}#{target.issue}: {status}")

    if any(status != "ok" for _, status in results):
        sys.exit(1)


if __name__ == "__main__":
    cli()
