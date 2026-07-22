"""CLI commands for the LLM evaluation tool."""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import sys
from dataclasses import dataclass

import click
from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.llm.client import create_llm_client
from craft_dashboard.llm.evaluator import IssueEvaluator
from craft_dashboard.settings import Settings
from rich.console import Console

from scripts.llm.console import setup_rich_logging
from scripts.llm.orchestrator import _evaluate_issues
from scripts.llm.pricing import format_usd
from scripts.llm.storage import _clear_evaluations, count_evaluations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluateOptions:
    """Options for a single ``evaluate`` CLI invocation.

    Replaces a long, error-prone positional argument list on ``_main`` --
    fields are matched by name against ``evaluate_cmd``'s Click option
    destinations (see ``_evaluate_options`` below), so adding a new CLI flag
    only requires adding one field here and one ``click.option`` call.
    """

    project: str = ""
    limit: int = 0
    open_only: bool = False
    verbose: bool = False
    force: bool = False
    issue: str = ""
    incomplete: bool = False
    stale_days: int = 0
    dry_run: bool = False
    strict_validation: bool = False
    no_resume: bool = False
    concurrency: int = 1
    no_progress: bool = False
    json_summary: bool = False


def _parse_issue_filter(issue_spec: str) -> list[tuple[str, int, int]]:
    """Parse --issue values into project/id ranges."""
    if not issue_spec:
        return []

    filters = []
    for item in issue_spec.split(","):
        part = item.strip()
        if "#" not in part:
            logger.error(
                "Invalid issue format: %s (expected project#id or project#min-max)",
                part,
            )
            continue

        project_name, id_part = part.split("#", 1)
        project_name = project_name.strip()

        if "-" in id_part:
            try:
                min_id_str, max_id_str = id_part.split("-", 1)
                filters.append(
                    (project_name, int(min_id_str.strip()), int(max_id_str.strip()))
                )
            except ValueError:
                logger.exception("Invalid issue range: %s", part)
        else:
            try:
                issue_id = int(id_part.strip())
                filters.append((project_name, issue_id, issue_id))
            except ValueError:
                logger.exception("Invalid issue ID: %s", part)

    return filters


async def _clear_main(project: str, yes: bool) -> None:
    """Confirm and clear stored LLM evaluations."""
    settings = Settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    try:
        count = await count_evaluations(
            project,
            session_factory=session_factory,
            engine=engine,
        )
        if count == 0:
            scope = f"for project '{project}'" if project else ""
            logger.info("No evaluations found %s. Nothing to clear.", scope)
            return

        scope = f"for project '{project}'" if project else "across ALL projects"
        if not yes:
            click.confirm(f"Delete {count:,} LLM evaluations {scope}?", abort=True)

        deleted = await _clear_evaluations(
            project,
            session_factory=session_factory,
            engine=engine,
        )
        logger.info("Cleared %d evaluations %s.", deleted, scope)
    finally:
        await engine.dispose()


async def _main(options: EvaluateOptions) -> dict:
    """Run LLM evaluation.

    Returns the evaluation stats dict so ``evaluate_cmd`` can derive its
    process exit code from ``stats["errored"]``.
    """
    settings = Settings()
    log_level = (
        logging.DEBUG
        if options.verbose
        else getattr(logging, settings.log_level.upper(), logging.INFO)
    )

    # Only show a live Rich progress bar when attached to a real terminal
    # (e.g. not when run as a detached subprocess by the admin re-evaluate
    # endpoint, which redirects stdout and expects plain log lines).
    show_progress = sys.stdout.isatty() and not options.no_progress
    console: Console | None = None
    if show_progress:
        console = Console()
        setup_rich_logging(verbose=options.verbose, console=console)
    logging.getLogger().setLevel(log_level)

    if not settings.openrouter_api_key:
        logger.error("OPENROUTER_API_KEY environment variable is not set.")
        sys.exit(1)

    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)
    client = create_llm_client(settings)
    evaluator = IssueEvaluator(
        client=client,
        model=settings.model,
    )

    issue_filters = None
    force = options.force
    if options.issue:
        issue_filters = _parse_issue_filter(options.issue)
        if not issue_filters:
            logger.error("No valid issue filters parsed from: %s", options.issue)
            sys.exit(1)
        force = True
        logger.info("Filtering to specific issues (force=True): %s", issue_filters)

    if options.incomplete or options.stale_days > 0:
        force = True

    logger.info(
        "Using openrouter backend (model=%s, open_only=%s, force=%s, concurrency=%d)",
        settings.model,
        options.open_only,
        force,
        options.concurrency,
    )

    try:
        stats = await _evaluate_issues(
            session_factory=session_factory,
            evaluator=evaluator,
            maintainers=set(config.maintainers),
            project_filter=options.project,
            limit=options.limit,
            open_only=options.open_only,
            force=force,
            issue_filters=issue_filters,
            incomplete=options.incomplete,
            stale_days=options.stale_days,
            dry_run=options.dry_run,
            llm_backend="openrouter",
            strict_validation=options.strict_validation,
            resume=not options.no_resume,
            concurrency=options.concurrency,
            console=console,
        )
        cost_note = ""
        if stats["estimated_cost_usd"] > 0 or stats["unpriced_evaluations"] > 0:
            cost_note = f", ~{format_usd(stats['estimated_cost_usd'])} estimated cost"
            if stats["unpriced_evaluations"] > 0:
                cost_note += (
                    f" ({stats['unpriced_evaluations']} evaluations excluded: "
                    f"no pricing data for model {settings.model!r})"
                )
        logger.info(
            "Evaluation complete: %d evaluated, %d skipped, %d errors, %d total tokens%s",
            stats["evaluated"],
            stats["skipped"],
            stats["errored"],
            stats["total_tokens"],
            cost_note,
        )
        if options.json_summary:
            # Emitted as a standalone JSON line, separate from the log lines
            # above (which admin.py's dry-run regex parsing depends on), so
            # this never disturbs that byte-for-byte matching.
            click.echo(json.dumps(stats))
    finally:
        await engine.dispose()

    return stats


def _evaluate_options(func: click.decorators.FC) -> click.decorators.FC:
    """Compose all of ``evaluate_cmd``'s CLI options into one decorator.

    Grouping every ``click.option`` call here (instead of stacking a dozen
    of them directly on ``evaluate_cmd``) keeps the command definition
    short and makes the full set of evaluate-time flags easy to reuse or
    inspect as a single unit; option destinations map 1:1 onto
    :class:`EvaluateOptions` fields.
    """
    options = (
        click.option(
            "--project", default="", help="Only evaluate issues for this project."
        ),
        click.option(
            "--limit", default=0, type=int, help="Max issues to evaluate (0=all)."
        ),
        click.option(
            "--open-only",
            is_flag=True,
            default=False,
            help="Only evaluate open issues (used for daily cron; default evaluates all).",
        ),
        click.option(
            "--verbose",
            "-v",
            is_flag=True,
            default=False,
            help="Enable debug logging (LLM prompts, token counts). Overrides LOG_LEVEL.",
        ),
        click.option(
            "--force",
            is_flag=True,
            default=False,
            help="Force re-evaluation of all matched issues (ignore content hash check).",
        ),
        click.option(
            "--issue",
            default="",
            help="Evaluate specific issues (e.g., charmcraft#2687,snapcraft#100-200). Implies --force.",
        ),
        click.option(
            "--incomplete",
            is_flag=True,
            default=False,
            help="Only evaluate issues with incomplete LLM data (missing summary, action, or scores).",
        ),
        click.option(
            "--stale-days",
            default=0,
            type=int,
            help="Only evaluate issues whose LLM evaluation is older than N days (0=disabled).",
        ),
        click.option(
            "--dry-run",
            is_flag=True,
            default=False,
            help="Show how many issues would be evaluated without actually running.",
        ),
        click.option(
            "--strict-validation",
            is_flag=True,
            default=False,
            help="Stop the run on the first LLM validation failure instead of skipping it.",
        ),
        click.option(
            "--no-resume",
            is_flag=True,
            default=False,
            help="Ignore any saved checkpoint and start a fresh evaluation run.",
        ),
        click.option(
            "--concurrency",
            default=1,
            type=int,
            help=(
                "Number of issues to evaluate concurrently (default: 1, sequential). "
                "Safe to raise against remote backends like OpenRouter; keep low "
                "against a single self-hosted local LLM endpoint."
            ),
        ),
        click.option(
            "--no-progress",
            is_flag=True,
            default=False,
            help=(
                "Disable the live Rich progress bar (shown automatically in an "
                "interactive terminal) and use plain text logs instead."
            ),
        ),
        click.option(
            "--json-summary",
            is_flag=True,
            default=False,
            help="Print a JSON summary of the evaluation stats to stdout when done.",
        ),
    )
    for option in reversed(options):
        func = option(func)
    return func


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """LLM evaluation tool for craft-dashboard issues and PRs."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command(name="evaluate")
@_evaluate_options
def evaluate_cmd(**kwargs: object) -> None:
    """Run LLM evaluation on issues and PRs."""
    options = EvaluateOptions(**kwargs)
    stats = asyncio.run(_main(options))
    if stats["errored"] > 0:
        sys.exit(1)


@cli.command(name="clear-evaluations")
@click.option(
    "--project",
    default="",
    help="Only clear evaluations for this project (default: all).",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt.",
)
def clear_evaluations_cmd(project: str, yes: bool) -> None:
    """Delete all LLM evaluations and reset token counts."""
    asyncio.run(_clear_main(project, yes))
