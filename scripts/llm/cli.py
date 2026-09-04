"""CLI commands for the LLM evaluation tool."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib

import click
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.settings import Settings
from dotenv import load_dotenv
from sqlalchemy import delete, func, select

from scripts.llm.eval_worker import run_evaluate_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")


async def _count_evaluations(session, project: str) -> int:
    """Count stored LLM evaluations, optionally scoped to one project."""
    if project:
        query = (
            select(func.count(LLMEvaluation.id))
            .join(Issue, LLMEvaluation.issue_id == Issue.id)
            .join(Project, Issue.project_id == Project.id)
            .where(Project.name == project)
        )
    else:
        query = select(func.count(LLMEvaluation.id))

    return await session.scalar(query) or 0


async def _delete_evaluations(session, project: str) -> int:
    """Delete stored LLM evaluations, optionally scoped to one project."""
    if project:
        issue_ids = (
            select(Issue.id)
            .join(Project, Issue.project_id == Project.id)
            .where(Project.name == project)
        )
        statement = delete(LLMEvaluation).where(LLMEvaluation.issue_id.in_(issue_ids))
    else:
        statement = delete(LLMEvaluation)

    result = await session.execute(statement)
    await session.commit()
    return result.rowcount or 0


async def _clear_main(project: str, yes: bool) -> None:
    """Confirm and clear stored LLM evaluations."""
    settings = Settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    try:
        async with session_factory() as session:
            count = await _count_evaluations(session, project)
            if count == 0:
                scope = f"for project '{project}'" if project else ""
                logger.info("No evaluations found %s. Nothing to clear.", scope)
                return

            scope = f"for project '{project}'" if project else "across ALL projects"
            if not yes:
                click.confirm(f"Delete {count:,} LLM evaluations {scope}?", abort=True)

            deleted = await _delete_evaluations(session, project)
            logger.info("Cleared %d evaluations %s.", deleted, scope)
    finally:
        await engine.dispose()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """LLM evaluation tool for craft-dashboard issues and PRs."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


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


@cli.command(name="evaluate")
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
    "--ca-cert",
    default="",
    envvar="LOCAL_LLM_CA_CERT",
    help="PEM CA cert path for the local LLM TLS certificate [env: LOCAL_LLM_CA_CERT]",
)
@click.option(
    "--server-ca-cert",
    default="",
    envvar="EVAL_CLIENT_SERVER_CA_CERT",
    help="PEM CA cert path for the craft-dashboard server TLS certificate [env: EVAL_CLIENT_SERVER_CA_CERT]",
)
@click.option(
    "--interval",
    "poll_interval",
    default=30,
    show_default=True,
    type=click.IntRange(min=1),
    help="Seconds between polls when no work is available",
)
@click.option(
    "--limit",
    "--max-evaluations",
    "limit",
    default=0,
    show_default=True,
    type=click.IntRange(min=0),
    help="Max evaluations before exit (0=unlimited). --max-evaluations is an alias.",
)
@click.option("--project", default="", help="Only evaluate issues for this project")
@click.option(
    "--open-only/--all-issues",
    default=True,
    show_default=True,
    help="Only evaluate open issues",
)
@click.option("--force", is_flag=True, default=False, help="Force re-evaluation")
@click.option(
    "--incomplete",
    is_flag=True,
    default=False,
    help="Only evaluate incomplete evaluations",
)
@click.option(
    "--stale-days",
    default=0,
    show_default=True,
    type=click.IntRange(min=0),
    help="Only evaluate stale evaluations older than N days",
)
@click.option(
    "--issue",
    default="",
    help="Evaluate a single issue/PR number for --project (for example --issue 1068). Implies --force and ignores --open-only/--incomplete/--stale-days.",
)
@click.option(
    "--concurrency",
    default=10,
    show_default=True,
    type=click.IntRange(min=1),
    help="Number of concurrent HTTP worker coroutines to run",
)
@click.option(
    "--llm-backend",
    type=click.Choice(["openrouter", "local"], case_sensitive=False),
    default="openrouter",
    show_default=True,
    help="Backend used for evaluation text generation; embeddings always use OpenRouter",
)
@click.option(
    "--embed-model",
    default="openai/text-embedding-3-small",
    show_default=True,
    help="OpenRouter embedding model for summary embeddings",
)
@click.option(
    "--verbose",
    "verbose",
    is_flag=True,
    default=False,
    help="Show timestamps, URLs, and model details",
)
@click.option(
    "--log",
    "log",
    is_flag=True,
    default=False,
    help="Write detailed debug traces and LLM logs to .logs/ directory",
)
def evaluate_cmd(
    server: str,
    token: str,
    ca_cert: str,
    server_ca_cert: str,
    poll_interval: int,
    limit: int,
    project: str,
    open_only: bool,
    force: bool,
    incomplete: bool,
    stale_days: int,
    issue: str,
    concurrency: int,
    llm_backend: str,
    embed_model: str,
    verbose: bool,
    log: bool,
) -> None:
    """Run the continuous HTTP-only evaluation service against /api/eval/*."""
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not openrouter_api_key:
        raise click.UsageError(
            "OPENROUTER_API_KEY is required because evaluate always computes OpenRouter embeddings."
        )
    if issue and not project:
        raise click.UsageError("--issue requires --project")

    if server_ca_cert:
        expanded_server_ca = pathlib.Path(server_ca_cert).expanduser()
        if not expanded_server_ca.is_file():
            raise click.UsageError(
                f"Server CA certificate file not found: '{server_ca_cert}' (resolved to '{expanded_server_ca}'). "
                "Check the --server-ca-cert option or EVAL_CLIENT_SERVER_CA_CERT in your .env file."
            )

    llm_backend = llm_backend.lower()
    llm_url = ""
    llm_api_key = ""
    if llm_backend == "local":
        llm_url = os.environ.get("LOCAL_LLM_URL", "")
        model = os.environ.get("LOCAL_LLM_MODEL", "")
        missing = [
            name
            for name, value in (("LOCAL_LLM_URL", llm_url), ("LOCAL_LLM_MODEL", model))
            if not value
        ]
        if missing:
            raise click.UsageError(
                f"Missing required environment variable(s): {', '.join(missing)}. Set them in your .env file."
            )
        if ca_cert:
            expanded_ca = pathlib.Path(ca_cert).expanduser()
            if not expanded_ca.is_file():
                raise click.UsageError(
                    f"CA certificate file not found: '{ca_cert}' (resolved to '{expanded_ca}'). "
                    "Check the --ca-cert option or LOCAL_LLM_CA_CERT in your .env file."
                )
        llm_api_key = os.environ.get("LOCAL_LLM_API_KEY", "")
        model_summary = model_scoring = model
    else:
        ca_cert = ""
        model_summary = os.environ.get("OPENROUTER_MODEL_SUMMARY", "")
        model_scoring = os.environ.get("OPENROUTER_MODEL_SCORING", "")
        missing = [
            name
            for name, value in (
                ("OPENROUTER_MODEL_SUMMARY", model_summary),
                ("OPENROUTER_MODEL_SCORING", model_scoring),
            )
            if not value
        ]
        if missing:
            raise click.UsageError(
                f"Missing required setting(s): {', '.join(missing)}. "
                "Set them in your .env file."
            )

    asyncio.run(
        run_evaluate_loop(
            server=server,
            token=token,
            model_summary=model_summary,
            model_scoring=model_scoring,
            llm_backend=llm_backend,
            llm_url=llm_url,
            llm_api_key=llm_api_key,
            ca_cert=ca_cert,
            poll_interval=poll_interval,
            limit=limit,
            project=project,
            open_only=open_only,
            force=force,
            incomplete=incomplete,
            stale_days=stale_days,
            server_ca_cert=server_ca_cert,
            verbose=verbose,
            openrouter_api_key=openrouter_api_key,
            embed_model=embed_model,
            issue=issue,
            concurrency=concurrency,
            log=log,
        )
    )
