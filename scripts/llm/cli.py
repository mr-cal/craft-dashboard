"""CLI commands for the LLM evaluation tool."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

import click
from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.llm.client import create_llm_client
from craft_dashboard.llm.evaluator import IssueEvaluator
from craft_dashboard.settings import Settings
from scripts.llm.orchestrator import _evaluate_issues
from scripts.llm.storage import _clear_evaluations, count_evaluations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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


async def _main(  # noqa: PLR0913
    project: str,
    limit: int,
    open_only: bool,
    verbose: bool,
    force: bool,
    issue: str,
    incomplete: bool,
    stale_days: int,
    dry_run: bool,
    strict_validation: bool,
    no_resume: bool,
) -> None:
    """Run LLM evaluation."""
    settings = Settings()
    log_level = (
        logging.DEBUG
        if verbose
        else getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    logging.getLogger().setLevel(log_level)

    if not settings.enable_server_eval:
        logger.info(
            "Server-side evaluation is disabled (ENABLE_SERVER_EVAL=false). "
            "Use the eval client script for pull-based evaluation instead."
        )
        return

    if not settings.openrouter_api_key:
        logger.error("OPENROUTER_API_KEY environment variable is not set.")
        sys.exit(1)

    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)
    client = create_llm_client(settings)
    evaluator = IssueEvaluator(
        client=client,
        summary_model=settings.summary_model,
        evaluation_model=settings.evaluation_model,
    )

    issue_filters = None
    if issue:
        issue_filters = _parse_issue_filter(issue)
        if not issue_filters:
            logger.error("No valid issue filters parsed from: %s", issue)
            sys.exit(1)
        force = True
        logger.info("Filtering to specific issues (force=True): %s", issue_filters)

    if incomplete or stale_days > 0:
        force = True

    logger.info(
        "Using openrouter backend (summary=%s, eval=%s, open_only=%s, force=%s)",
        settings.summary_model,
        settings.evaluation_model,
        open_only,
        force,
    )

    try:
        stats = await _evaluate_issues(
            session_factory=session_factory,
            evaluator=evaluator,
            maintainers=set(config.maintainers),
            project_filter=project,
            limit=limit,
            open_only=open_only,
            force=force,
            issue_filters=issue_filters,
            incomplete=incomplete,
            stale_days=stale_days,
            dry_run=dry_run,
            llm_backend="openrouter",
            strict_validation=strict_validation,
            resume=not no_resume,
        )
        logger.info(
            "Evaluation complete: %d evaluated, %d skipped, %d errors, %d total tokens",
            stats["evaluated"],
            stats["skipped"],
            stats["errored"],
            stats["total_tokens"],
        )
    finally:
        await engine.dispose()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """LLM evaluation tool for craft-dashboard issues and PRs."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command(name="evaluate")
@click.option("--project", default="", help="Only evaluate issues for this project.")
@click.option("--limit", default=0, type=int, help="Max issues to evaluate (0=all).")
@click.option(
    "--open-only",
    is_flag=True,
    default=False,
    help="Only evaluate open issues (used for daily cron; default evaluates all).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging (LLM prompts, token counts). Overrides LOG_LEVEL.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Force re-evaluation of all matched issues (ignore content hash check).",
)
@click.option(
    "--issue",
    default="",
    help="Evaluate specific issues (e.g., charmcraft#2687,snapcraft#100-200). Implies --force.",
)
@click.option(
    "--incomplete",
    is_flag=True,
    default=False,
    help="Only evaluate issues with incomplete LLM data (missing summary, action, or scores).",
)
@click.option(
    "--stale-days",
    default=0,
    type=int,
    help="Only evaluate issues whose LLM evaluation is older than N days (0=disabled).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show how many issues would be evaluated without actually running.",
)
@click.option(
    "--strict-validation",
    is_flag=True,
    default=False,
    help="Stop the run on the first LLM validation failure instead of skipping it.",
)
@click.option(
    "--no-resume",
    is_flag=True,
    default=False,
    help="Ignore any saved checkpoint and start a fresh evaluation run.",
)
def evaluate_cmd(  # noqa: PLR0913
    project: str,
    limit: int,
    open_only: bool,
    verbose: bool,
    force: bool,
    issue: str,
    incomplete: bool,
    stale_days: int,
    dry_run: bool,
    strict_validation: bool,
    no_resume: bool,
) -> None:
    """Run LLM evaluation on issues and PRs."""
    asyncio.run(
        _main(
            project,
            limit,
            open_only,
            verbose,
            force,
            issue,
            incomplete,
            stale_days,
            dry_run,
            strict_validation,
            no_resume,
        )
    )


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
