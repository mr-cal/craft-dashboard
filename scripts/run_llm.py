#!/usr/bin/env python3
"""LLM evaluation entry point for cron jobs.

Evaluates issues/PRs that have changed since last evaluation.
By default evaluates all issues (open and closed). Use --open-only
for the daily cron job. Use --backend local for a local LLM server.

Usage:
    uv run scripts/run_llm.py evaluate                          # all issues, openrouter
    uv run scripts/run_llm.py evaluate --open-only              # open issues only (cron)
    uv run scripts/run_llm.py evaluate --force                  # re-evaluate all
    uv run scripts/run_llm.py evaluate --issue charmcraft#2687  # specific issue
    uv run scripts/run_llm.py evaluate --incomplete             # missing data only
    uv run scripts/run_llm.py evaluate --stale-days 30          # stale evaluations
    uv run scripts/run_llm.py evaluate --dry-run                # preview count
    uv run scripts/run_llm.py clear-evaluations                 # delete all evaluations
    uv run scripts/run_llm.py clear-evaluations --project snap  # delete for one project

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
    LLM_BACKEND: "openrouter" or "local" (default: openrouter)
    OPENROUTER_API_KEY: OpenRouter API key (required when LLM_BACKEND=openrouter)
    LOCAL_LLM_URL: Local LLM base URL (default: http://localhost:11434/v1)
"""

import asyncio
import logging
import pathlib
import sys
from datetime import UTC, datetime

import click

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.llm.client import QuotaExhaustedError, create_llm_client
from craft_dashboard.llm.evaluator import IssueEvaluator
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _evaluate_issues(  # noqa: PLR0913
    session_factory,
    evaluator: IssueEvaluator,
    maintainers: set[str],
    project_filter: str = "",
    limit: int = 0,
    open_only: bool = False,
    force: bool = False,
    issue_filters: list[tuple[str, int, int]] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
    dry_run: bool = False,
    llm_backend: str = "openrouter",
) -> dict[str, int]:
    """Evaluate issues/PRs that need re-evaluation.

    By default evaluates all issues (open and closed). Pass open_only=True
    for the daily cron run which only needs to catch newly-changed open issues.

    Args:
        session_factory: Async session factory.
        evaluator: The IssueEvaluator instance.
        maintainers: Set of maintainer usernames.
        project_filter: Optional project name filter.
        limit: Max issues to evaluate (0 = unlimited).
        open_only: If True, only evaluate open issues.
        force: If True, re-evaluate all matched issues (ignore content hash).
        issue_filters: Optional list of (project_name, min_id, max_id) tuples for --issue filtering.
        incomplete: If True, only evaluate issues with incomplete LLM data.
        stale_days: Only evaluate issues older than N days (0 = disabled).
        dry_run: If True, show count without evaluating.
        llm_backend: The LLM backend used ("openrouter" or "local").

    Returns:
        Stats dict with evaluated, skipped, errored counts.

    """
    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.llm_evaluation import (
        LLMEvaluation,
    )
    from craft_dashboard.models.project import (
        Project,
    )
    from sqlalchemy import or_, select
    from sqlalchemy.dialects.postgresql import insert

    stats = {"evaluated": 0, "skipped": 0, "errored": 0, "total_tokens": 0}

    async with session_factory() as session:
        query = (
            select(
                Issue,
                Project.name.label("project_name"),
                LLMEvaluation.issue_data_hash,
            )
            .join(Project, Issue.project_id == Project.id)
            .outerjoin(
                LLMEvaluation,
                (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest.is_(True),
            )
        )

        if open_only:
            query = query.where(Issue.state == "open")

        if project_filter:
            query = query.where(Project.name == project_filter)

        # Apply --issue filters if specified
        if issue_filters:
            from sqlalchemy import Integer, and_, cast

            conditions = []
            for project_name, min_id, max_id in issue_filters:
                # external_id is VARCHAR, so cast to Integer for numeric comparison.
                ext_id_int = cast(Issue.external_id, Integer)
                conditions.append(
                    and_(
                        Project.name == project_name,
                        ext_id_int >= min_id,
                        ext_id_int <= max_id,
                    )
                )
            query = query.where(or_(*conditions))

        # Apply --incomplete filter
        if incomplete:
            query = query.where(
                or_(
                    LLMEvaluation.issue_id.is_(None),  # never evaluated
                    LLMEvaluation.summary.is_(None),
                    LLMEvaluation.summary == "",
                    LLMEvaluation.suggested_action.is_(None),
                    LLMEvaluation.suggested_action == "",
                    LLMEvaluation.scores.is_(None),
                )
            )

        # Apply --stale-days filter
        if stale_days > 0:
            from datetime import timedelta

            cutoff = datetime.now(tz=UTC) - timedelta(days=stale_days)
            query = query.where(
                or_(
                    LLMEvaluation.issue_id.is_(None),  # never evaluated
                    LLMEvaluation.evaluated_at < cutoff,
                )
            )

        result = await session.execute(query)
        rows = result.all()

    # Dry-run: show count and exit
    if dry_run:
        logger.info("DRY RUN: %d issues would be evaluated", len(rows))
        return {
            "evaluated": 0,
            "skipped": 0,
            "errored": 0,
            "total_tokens": 0,
            "would_evaluate": len(rows),
        }

    total_to_eval = len(rows)
    logger.info("Starting evaluation of %d issues", total_to_eval)

    for idx, row in enumerate(rows):
        issue = row[0]
        # If --force is used, pass None to skip hash check
        existing_hash = None if force else row.issue_data_hash
        labels = issue.labels if isinstance(issue.labels, list) else []

        now = datetime.now(tz=UTC)
        created = issue.created_at or now
        updated = issue.updated_at or now
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)

        age_days = (now - created).days
        last_activity_days = (now - updated).days

        issue_comments = issue.comments if issue.comments else []
        pr_details = (
            issue.metadata_
            if issue.issue_type == "pull_request" and issue.metadata_
            else None
        )

        project_name = row.project_name
        issue_ref = f"{project_name}#{issue.external_id}"
        logger.info(
            "[%d/%d] Evaluating %s: %s",
            idx + 1,
            total_to_eval,
            issue_ref,
            issue.title[:60],
        )

        try:
            result = await evaluator.evaluate_issue(
                title=issue.title,
                body=issue.body,
                issue_type=issue.issue_type,
                labels=labels,
                age_days=age_days,
                last_activity_days=last_activity_days,
                author=issue.author or "unknown",
                is_maintainer=issue.author in maintainers if issue.author else False,
                comment_count=len(issue_comments),
                comments=issue_comments,
                pr_details=pr_details,
                existing_hash=existing_hash,
            )
        except QuotaExhaustedError:
            logger.warning(
                "OpenRouter daily quota exhausted. Stopping evaluation. "
                "%d evaluated so far.",
                stats["evaluated"],
            )
            break
        except Exception:
            logger.exception("Error evaluating issue %s", issue.title)
            stats["errored"] += 1
            processed = stats["evaluated"] + stats["skipped"] + stats["errored"]
            if processed % 10 == 0:
                logger.info(
                    "Progress [%d/%d]: %d evaluated, %d skipped, %d errors",
                    processed,
                    total_to_eval,
                    stats["evaluated"],
                    stats["skipped"],
                    stats["errored"],
                )
            continue

        if result is None:
            logger.info(
                "Skipped %s (content unchanged): %s", issue_ref, issue.title[:60]
            )
            stats["skipped"] += 1
            processed = stats["evaluated"] + stats["skipped"] + stats["errored"]
            if processed % 10 == 0:
                logger.info(
                    "Progress [%d/%d]: %d evaluated, %d skipped, %d errors",
                    processed,
                    total_to_eval,
                    stats["evaluated"],
                    stats["skipped"],
                    stats["errored"],
                )
            continue

        # Upsert evaluation: mark previous evaluation as not-latest, then insert new one
        async with session_factory() as session:
            # Mark previous evaluation(s) for this issue as not latest
            from sqlalchemy import (
                update as sa_update,
            )

            await session.execute(
                sa_update(LLMEvaluation)
                .where(
                    LLMEvaluation.issue_id == issue.id, LLMEvaluation.latest.is_(True)
                )
                .values(latest=False)
            )
            # Insert new evaluation with latest=True
            await session.execute(
                insert(LLMEvaluation).values(
                    issue_id=issue.id,
                    model_name=evaluator.evaluation_model,
                    summary=result["summary"],
                    suggested_action=result["suggested_action"],
                    suggested_action_reason=result["suggested_action_reason"],
                    scores=result["scores"],
                    tokens_used=result["tokens_used"],
                    prompt_tokens=result["prompt_tokens"],
                    completion_tokens=result["completion_tokens"],
                    llm_backend=llm_backend,
                    evaluated_at=datetime.now(tz=UTC),
                    issue_data_hash=result["issue_data_hash"],
                    latest=True,
                )
            )
            await session.commit()

        stats["evaluated"] += 1
        stats["total_tokens"] += result["tokens_used"]
        logger.info(
            "[%d/%d] Evaluated %s (%s, %d tokens): %s",
            stats["evaluated"],
            total_to_eval,
            issue_ref,
            result["suggested_action"],
            result["tokens_used"],
            issue.title[:60],
        )

        processed = stats["evaluated"] + stats["skipped"] + stats["errored"]
        if processed % 10 == 0:
            logger.info(
                "Progress [%d/%d]: %d evaluated, %d skipped, %d errors",
                processed,
                total_to_eval,
                stats["evaluated"],
                stats["skipped"],
                stats["errored"],
            )

        # Check limit after successful evaluation (not after skips)
        if limit > 0 and stats["evaluated"] >= limit:
            logger.info("Reached evaluation limit of %d", limit)
            break

    return stats


def _parse_issue_filter(issue_spec: str) -> list[tuple[str, int, int]]:
    """Parse --issue argument into list of (project, min_id, max_id) tuples.

    Examples:
        "charmcraft#2687" -> [("charmcraft", 2687, 2687)]
        "charmcraft#100-200" -> [("charmcraft", 100, 200)]
        "charmcraft#2687,snapcraft#100-200" -> [("charmcraft", 2687, 2687), ("snapcraft", 100, 200)]

    Args:
        issue_spec: Comma-separated issue references.

    Returns:
        List of (project_name, min_external_id, max_external_id) tuples.

    """
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
            # Range: charmcraft#100-200
            try:
                min_id_str, max_id_str = id_part.split("-", 1)
                min_id = int(min_id_str.strip())
                max_id = int(max_id_str.strip())
                filters.append((project_name, min_id, max_id))
            except ValueError as e:
                logger.error("Invalid issue range: %s (%s)", part, e)  # noqa: TRY400
        else:
            # Single issue: charmcraft#2687
            try:
                issue_id = int(id_part.strip())
                filters.append((project_name, issue_id, issue_id))
            except ValueError as e:
                logger.error("Invalid issue ID: %s (%s)", part, e)  # noqa: TRY400

    return filters


async def _main(  # noqa: PLR0913
    project: str,
    limit: int,
    backend: str,
    open_only: bool,
    verbose: bool,
    force: bool,
    issue: str,
    incomplete: bool,
    stale_days: int,
    dry_run: bool,
) -> None:
    """Run LLM evaluation."""
    settings = Settings()

    log_level = (
        logging.DEBUG
        if verbose
        else getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    logging.getLogger().setLevel(log_level)

    # Allow CLI flag to override settings
    if backend:
        settings = settings.model_copy(update={"llm_backend": backend})

    if settings.llm_backend == "openrouter" and not settings.openrouter_api_key:
        logger.error("OPENROUTER_API_KEY environment variable is not set.")
        sys.exit(1)

    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    client = create_llm_client(settings)

    # Select model names based on backend
    summary_model = settings.summary_model
    evaluation_model = settings.evaluation_model

    evaluator = IssueEvaluator(
        client=client,
        summary_model=summary_model,
        evaluation_model=evaluation_model,
    )

    # Parse --issue filters and imply --force when --issue is used
    issue_filters = None
    if issue:
        issue_filters = _parse_issue_filter(issue)
        if not issue_filters:
            logger.error("No valid issue filters parsed from: %s", issue)
            sys.exit(1)
        # When --issue is specified, always force re-evaluation
        force = True
        logger.info("Filtering to specific issues (force=True): %s", issue_filters)

    # Imply --force when --incomplete or --stale-days is used
    if incomplete or stale_days > 0:
        force = True

    logger.info(
        "Using %s backend (summary=%s, eval=%s, open_only=%s, force=%s)",
        settings.llm_backend,
        summary_model,
        evaluation_model,
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
            llm_backend=settings.llm_backend,
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
    "--backend",
    type=click.Choice(["openrouter", "local"]),
    default=None,
    help="LLM backend to use (overrides LLM_BACKEND env var).",
)
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
def evaluate_cmd(  # noqa: PLR0913
    project: str,
    limit: int,
    backend: str,
    open_only: bool,
    verbose: bool,
    force: bool,
    issue: str,
    incomplete: bool,
    stale_days: int,
    dry_run: bool,
) -> None:
    """Run LLM evaluation on issues and PRs.

    By default evaluates all issues (open and closed). Use --open-only
    for the daily cron job which only needs to catch changed open issues.
    Use --backend local to run against a locally-hosted OpenAI-compatible LLM server
    without spending tokens on OpenRouter.

    Use --force to re-evaluate all matched issues even if content hasn't changed.
    Use --issue to evaluate specific issues by reference (implies --force).
    """
    asyncio.run(
        _main(
            project,
            limit,
            backend,
            open_only,
            verbose,
            force,
            issue,
            incomplete,
            stale_days,
            dry_run,
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
    """Delete all LLM evaluations and reset token counts.

    Removes all rows from llm_evaluations. Use --project to limit
    the scope to a specific project. Requires confirmation unless --yes.
    """
    asyncio.run(_clear_evaluations(project, yes))


async def _clear_evaluations(project: str, yes: bool) -> None:
    """Clear LLM evaluations from the database."""
    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.llm_evaluation import LLMEvaluation
    from craft_dashboard.models.project import Project
    from sqlalchemy import delete, func
    from sqlalchemy import select as sa_select

    settings = Settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    try:
        async with session_factory() as session:
            # Count evaluations to be deleted
            if project:
                count_q = (
                    sa_select(func.count(LLMEvaluation.id))
                    .join(Issue, LLMEvaluation.issue_id == Issue.id)
                    .join(Project, Issue.project_id == Project.id)
                    .where(Project.name == project)
                )
            else:
                count_q = sa_select(func.count(LLMEvaluation.id))
            count = await session.scalar(count_q) or 0

            if count == 0:
                scope = f"for project '{project}'" if project else ""
                logger.info("No evaluations found %s. Nothing to clear.", scope)
                return

            scope = f"for project '{project}'" if project else "across ALL projects"
            if not yes:
                click.confirm(
                    f"Delete {count:,} LLM evaluations {scope}?",
                    abort=True,
                )

            if project:
                # Delete evaluations for issues belonging to the project
                issue_ids = (
                    sa_select(Issue.id)
                    .join(Project, Issue.project_id == Project.id)
                    .where(Project.name == project)
                )
                stmt = delete(LLMEvaluation).where(
                    LLMEvaluation.issue_id.in_(issue_ids)
                )
            else:
                stmt = delete(LLMEvaluation)

            result = await session.execute(stmt)
            await session.commit()
            logger.info("Cleared %d evaluations %s.", result.rowcount, scope)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    cli()
