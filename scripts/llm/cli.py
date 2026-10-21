"""CLI commands for the LLM evaluation tool."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import os
import pathlib
import sys
import time
from typing import Any

import click
import httpx
from dotenv import load_dotenv

from scripts.llm.eval_startup import resolve_server_verify
from scripts.llm.eval_worker import run_evaluate_loop
from scripts.llm.worker_version import get_worker_version

HTTP_OK = httpx.codes.OK
HTTP_UPGRADE_REQUIRED = httpx.codes.UPGRADE_REQUIRED
HTTP_UNAUTHORIZED = httpx.codes.UNAUTHORIZED
HTTP_FORBIDDEN = httpx.codes.FORBIDDEN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _maybe_load_env_file(env_file: str | None) -> None:
    """Load environment variables from an explicit file if provided."""
    if env_file:
        expanded = pathlib.Path(env_file).expanduser()
        if not expanded.is_file():
            _handle_fatal_config_error(f"Environment file not found: {env_file}")
        load_dotenv(expanded)


def _resolve_auth(
    server: str = "",
    token: str = "",
    server_ca_cert: str = "",
) -> tuple[str, str, str]:
    """Resolve server URL, token, and CA certificate from CLI options or environment."""
    server = (
        server
        or os.environ.get("CRAFT_DASHBOARD_URL")
        or os.environ.get("DASHBOARD_URL")
        or ""
    )
    token = (
        token
        or os.environ.get("CRAFT_EVAL_TOKEN")
        or os.environ.get("EVAL_API_TOKEN")
        or ""
    )
    server_ca_cert = (
        server_ca_cert
        or os.environ.get("CRAFT_DASHBOARD_CA_CERT")
        or os.environ.get("DASHBOARD_CA_CERT")
        or ""
    )
    return server, token, server_ca_cert


async def _count_evaluations(
    http_client: httpx.AsyncClient,
    project: str = "",
    headers: dict[str, str] | None = None,
    *,
    issue: str = "",
    evaluated_by: str = "",
    stale_days: int = 0,
    incomplete: bool = False,
) -> int:
    """Count stored LLM evaluations via HTTP API, optionally scoped by fine-grained filters."""
    params: dict[str, Any] = {}
    if project:
        params["project"] = project
    if issue:
        params["issue"] = issue
    if evaluated_by:
        params["evaluated_by"] = evaluated_by
    if stale_days > 0:
        params["stale_days"] = stale_days
    if incomplete:
        params["incomplete"] = True
    resp = await http_client.get(
        "/api/eval/clear", params=params, headers=headers or {}
    )
    resp.raise_for_status()
    data = resp.json()
    return int(data.get("count", 0))


async def _delete_evaluations(
    http_client: httpx.AsyncClient,
    project: str = "",
    headers: dict[str, str] | None = None,
    *,
    issue: str = "",
    evaluated_by: str = "",
    stale_days: int = 0,
    incomplete: bool = False,
) -> int:
    """Delete stored LLM evaluations via HTTP API, optionally scoped by fine-grained filters."""
    params: dict[str, Any] = {}
    if project:
        params["project"] = project
    if issue:
        params["issue"] = issue
    if evaluated_by:
        params["evaluated_by"] = evaluated_by
    if stale_days > 0:
        params["stale_days"] = stale_days
    if incomplete:
        params["incomplete"] = True
    resp = await http_client.request(
        "DELETE", "/api/eval/clear", params=params, headers=headers or {}
    )
    resp.raise_for_status()
    data = resp.json()
    return int(data.get("deleted", 0))


async def _clear_main(
    project: str = "",
    yes: bool = False,
    issue: str = "",
    evaluated_by: str = "",
    stale_days: int = 0,
    incomplete: bool = False,
    server: str = "",
    token: str = "",
    server_ca_cert: str = "",
    env_file: str | None = None,
) -> None:
    """Confirm and clear stored LLM evaluations via HTTP API."""
    _maybe_load_env_file(env_file)
    server, token, server_ca_cert = _resolve_auth(server, token, server_ca_cert)

    missing = []
    if not server:
        missing.append("DASHBOARD_URL")
    if not token:
        missing.append("EVAL_API_TOKEN")
    if missing:
        _handle_fatal_config_error(
            f"Missing required authentication: {', '.join(missing)}. "
            "Please pass --token/--server or ensure CRAFT_EVAL_TOKEN/CRAFT_DASHBOARD_URL are set."
        )

    if issue and not project:
        _handle_fatal_config_error("--issue requires --project")

    server_url = server.rstrip("/")
    verify = resolve_server_verify(server_ca_cert)

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Worker-Version": get_worker_version(),
    }
    try:
        async with httpx.AsyncClient(
            base_url=server_url, timeout=30.0, verify=verify
        ) as http_client:
            count = await _count_evaluations(
                http_client,
                project=project,
                headers=headers,
                issue=issue,
                evaluated_by=evaluated_by,
                stale_days=stale_days,
                incomplete=incomplete,
            )

            parts = []
            if project:
                parts.append(f"for project '{project}'")
            if issue:
                parts.append(f"issue #{issue}")
            if evaluated_by:
                parts.append(f"evaluated by '{evaluated_by}'")
            if stale_days > 0:
                parts.append(f"stale > {stale_days} days")
            if incomplete:
                parts.append("incomplete only")
            scope = (
                f" ({', '.join(parts)})" if parts else " across ALL projects and tokens"
            )

            if count == 0:
                logger.info("No evaluations found%s. Nothing to clear.", scope)
                return

            if not yes:
                click.confirm(f"Delete {count:,} LLM evaluations{scope}?", abort=True)

            deleted = await _delete_evaluations(
                http_client,
                project=project,
                headers=headers,
                issue=issue,
                evaluated_by=evaluated_by,
                stale_days=stale_days,
                incomplete=incomplete,
            )
            logger.info("Cleared %d evaluations%s.", deleted, scope)
    except httpx.HTTPStatusError as exc:
        raise click.ClickException(
            f"Eval API request failed with status {exc.response.status_code}: {exc.response.text}"
        ) from exc
    except httpx.RequestError as exc:
        raise click.ClickException(
            f"Failed to connect to dashboard server at {server_url}: {exc}"
        ) from exc


async def _status_main(
    *,
    server: str,
    token: str,
    server_ca_cert: str,
    env_file: str | None,
    verbose: bool,
) -> None:
    """Run diagnostics on dashboard server, token auth, and worker compatibility."""
    if verbose:
        logger.setLevel(logging.DEBUG)
    _maybe_load_env_file(env_file)
    server, token, server_ca_cert = _resolve_auth(server, token, server_ca_cert)
    server = server or "https://craft-dashboard.name"
    worker_ver = get_worker_version()

    click.echo("Craft Dashboard Evaluation Diagnostics:")
    click.echo(f"  Worker Version: {worker_ver}")
    click.echo(f"  Server URL:     {server}")

    if not token:
        click.secho(
            "  Token Auth:     [MISSING] Specify --token or set CRAFT_EVAL_TOKEN (or EVAL_API_TOKEN)",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    server_url = server.rstrip("/")
    verify = resolve_server_verify(server_ca_cert) if server_ca_cert else True
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Worker-Version": worker_ver,
    }

    started_at = time.monotonic()
    async with httpx.AsyncClient(
        base_url=server_url, timeout=15.0, verify=verify
    ) as http_client:
        try:
            resp = await http_client.get("/api/eval/status", headers=headers)
            elapsed_ms = (time.monotonic() - started_at) * 1000

            if resp.status_code == HTTP_OK:
                data = resp.json()
                auth_id = data.get("authenticated_as", "unknown")
                perms = ", ".join(data.get("permissions", []))
                click.secho(
                    f"  Server Conn:    [OK] Connected ({elapsed_ms:.0f}ms)",
                    fg="green",
                )
                click.secho(
                    f"  Token Auth:     [OK] Authenticated as '{auth_id}' (permissions: {perms})",
                    fg="green",
                )
                click.secho(
                    f"  Version Check:  [OK] Compatible (server minimum: {data.get('min_worker_version')})",
                    fg="green",
                )
                click.echo(
                    f"  Queue Status:   {data.get('pending', 0):,} issues pending / "
                    f"{data.get('total_open', 0):,} open issues "
                    f"({data.get('total_evaluated', 0):,} total evaluated, "
                    f"{data.get('evaluated_today', 0):,} today)"
                )
            elif resp.status_code == HTTP_UPGRADE_REQUIRED:
                err_data = (
                    resp.json()
                    if resp.headers.get("content-type", "").startswith(
                        "application/json"
                    )
                    else {}
                )
                detail_val = err_data.get("detail", resp.text)
                if isinstance(detail_val, dict):
                    detail = detail_val.get("detail", str(detail_val))
                    remedy = detail_val.get(
                        "remedy",
                        err_data.get(
                            "remedy", "Run 'uvx --upgrade craft-eval' to update."
                        ),
                    )
                else:
                    detail = str(detail_val)
                    remedy = err_data.get(
                        "remedy", "Run 'uvx --upgrade craft-eval' to update."
                    )
                click.secho(
                    f"  Version Check:  [UPGRADE REQUIRED] {detail}",
                    fg="red",
                    bold=True,
                )
                click.secho(f"  Remedy:         {remedy}", fg="yellow")
                sys.exit(1)
            elif resp.status_code == HTTP_UNAUTHORIZED:
                click.secho(
                    "  Token Auth:     [UNAUTHORIZED] Token is invalid or revoked.",
                    fg="red",
                    bold=True,
                )
                sys.exit(1)
            elif resp.status_code == HTTP_FORBIDDEN:
                click.secho(
                    "  Token Auth:     [FORBIDDEN] Token lacks required permissions.",
                    fg="red",
                    bold=True,
                )
                sys.exit(1)
            else:
                click.secho(
                    f"  Server Status:  [ERROR] HTTP {resp.status_code}: {resp.text[:200]}",
                    fg="red",
                )
                sys.exit(1)
        except httpx.ConnectError as exc:
            click.secho(
                f"  Server Conn:    [FAILED] Could not connect to {server_url}: {exc}",
                fg="red",
            )
            sys.exit(1)
        except (httpx.HTTPError, OSError) as exc:
            click.secho(f"  Server Conn:    [ERROR] {exc}", fg="red")
            sys.exit(1)

        # Check local / configured LLM endpoint
        copilot_model = os.environ.get("COPILOT_ACP_MODEL", "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        if copilot_model:
            click.secho(
                f"  LLM Backend:    [OK] Copilot ACP (model: {copilot_model})",
                fg="green",
            )
        elif llm_base_url:
            backend_type = (
                "OpenRouter"
                if "openrouter.ai" in llm_base_url.lower()
                else "Local/Custom"
            )
            click.secho(
                f"  LLM Backend:    [CONFIGURED] {backend_type} ({llm_base_url})",
                fg="green",
            )
        else:
            click.secho(
                "  LLM Backend:    [NOT CONFIGURED] Neither COPILOT_ACP_MODEL nor LLM_BASE_URL is set.",
                fg="yellow",
            )

        # Test tracked projects API
        with contextlib.suppress(httpx.HTTPError, OSError):
            rel_resp = await http_client.get("/api/eval/projects", headers=headers)
            if rel_resp.status_code == HTTP_OK:
                proj_count = len(rel_resp.json().get("projects", {}))
                click.secho(
                    f"  Projects API:   [OK] {proj_count} tracked projects accessible",
                    fg="green",
                )

    click.secho("\nAll core checks passed! System ready for evaluations.", fg="green")


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """LLM evaluation tool for craft-dashboard issues and PRs."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command(name="clear")
@click.option(
    "--server",
    default="",
    help="Craft Dashboard server URL (or CRAFT_DASHBOARD_URL / DASHBOARD_URL). Default: https://craft-dashboard.name",
)
@click.option(
    "--token",
    default="",
    help="Evaluation API token (or CRAFT_EVAL_TOKEN / EVAL_API_TOKEN).",
)
@click.option(
    "--ca-cert",
    "server_ca_cert",
    default="",
    help="Path to custom CA certificate for dashboard server.",
)
@click.option(
    "--env-file",
    default=None,
    help="Explicit path to environment file to load variables from.",
)
@click.option(
    "--project",
    default="",
    help="Only clear evaluations for this project (default: all).",
)
@click.option(
    "--issue",
    default="",
    help="Only clear evaluations for this issue number (requires --project).",
)
@click.option(
    "--evaluated-by",
    default="",
    help="Only clear evaluations produced by this token ID (admins only, or caller's own token ID).",
)
@click.option(
    "--stale-days",
    default=0,
    type=click.IntRange(min=0),
    help="Only clear evaluations older than N days.",
)
@click.option(
    "--incomplete",
    is_flag=True,
    default=False,
    help="Only clear incomplete evaluations.",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip confirmation prompt.",
)
def clear_evaluations_cmd(
    project: str,
    yes: bool,
    issue: str = "",
    evaluated_by: str = "",
    stale_days: int = 0,
    incomplete: bool = False,
    server: str = "",
    token: str = "",
    server_ca_cert: str = "",
    env_file: str | None = None,
) -> None:
    """Delete stored LLM evaluations with optional fine-grained filters."""
    asyncio.run(
        _clear_main(
            project=project,
            yes=yes,
            issue=issue,
            evaluated_by=evaluated_by,
            stale_days=stale_days,
            incomplete=incomplete,
            server=server,
            token=token,
            server_ca_cert=server_ca_cert,
            env_file=env_file,
        )
    )


_clear_alias = copy.copy(clear_evaluations_cmd)
_clear_alias.hidden = True
cli.add_command(_clear_alias, name="clear-evaluations")


@cli.command(name="status")
@click.option(
    "--server",
    default="",
    help="Craft Dashboard server URL (or CRAFT_DASHBOARD_URL / DASHBOARD_URL). Default: https://craft-dashboard.name",
)
@click.option(
    "--token",
    default="",
    help="Evaluation API token (or CRAFT_EVAL_TOKEN / EVAL_API_TOKEN).",
)
@click.option(
    "--ca-cert",
    "server_ca_cert",
    default="",
    help="Path to custom CA certificate for dashboard server.",
)
@click.option(
    "--env-file",
    default=None,
    help="Explicit path to environment file to load variables from.",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Show verbose diagnostics details.",
)
def status_cmd(
    server: str,
    token: str,
    server_ca_cert: str,
    env_file: str | None,
    verbose: bool,
) -> None:
    """Check connectivity, token authentication, worker compatibility, and queue status."""
    asyncio.run(
        _status_main(
            server=server,
            token=token,
            server_ca_cert=server_ca_cert,
            env_file=env_file,
            verbose=verbose,
        )
    )


def _handle_fatal_config_error(message: str) -> None:
    """Sleep before raising UsageError to prevent tight restart loops in container environments."""
    delay_str = os.environ.get("LLM_CONFIG_ERROR_DELAY_SECONDS", "15")
    try:
        delay = float(delay_str)
    except ValueError:
        delay = 15.0
    if delay > 0 and not os.environ.get("PYTEST_CURRENT_TEST"):
        logger.error(
            "%s Sleeping for %gs before exit to prevent tight restart loop.",
            message,
            delay,
        )
        time.sleep(delay)
    raise click.UsageError(message)


@cli.command(name="run")
@click.option(
    "--server",
    default="",
    help="Craft Dashboard server URL (or CRAFT_DASHBOARD_URL / DASHBOARD_URL). Default: https://craft-dashboard.name",
)
@click.option(
    "--token",
    default="",
    help="Evaluation API token (or CRAFT_EVAL_TOKEN / EVAL_API_TOKEN).",
)
@click.option(
    "--ca-cert",
    "server_ca_cert",
    default="",
    help="Path to custom CA certificate for dashboard server.",
)
@click.option(
    "--env-file",
    default=None,
    help="Explicit path to environment file to load variables from.",
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
    "--open-only",
    is_flag=True,
    default=False,
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
    help="Evaluate a single issue/PR number for --project. Implies --force and ignores --open-only/--incomplete/--stale-days.",
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
    type=click.Choice(["openrouter", "local", "copilot-acp"], case_sensitive=False),
    default=None,
    help="Optional backend override (auto-detected from LLM_BASE_URL or COPILOT_ACP_MODEL)",
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
@click.option(
    "--slow-eval",
    is_flag=True,
    default=False,
    help="Run evaluations at a slow, human-like pace with randomized cooldown delays (enforces concurrency=1).",
)
@click.option(
    "--min-delay",
    default=25.0,
    show_default=True,
    type=click.FloatRange(min=0.0),
    help="Minimum delay in seconds between evaluations when --slow-eval is enabled.",
)
@click.option(
    "--max-delay",
    default=55.0,
    show_default=True,
    type=click.FloatRange(min=0.0),
    help="Maximum delay in seconds between evaluations when --slow-eval is enabled.",
)
@click.option(
    "--tool-delay",
    default=None,
    type=click.FloatRange(min=0.0),
    help="Delay in seconds between intra-issue tool calling rounds (defaults to 7.0s when --slow-eval is enabled, 0.0s otherwise).",
)
def evaluate_cmd(
    server: str,
    token: str,
    server_ca_cert: str,
    env_file: str | None,
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
    verbose: bool,
    log: bool,
    slow_eval: bool,
    min_delay: float,
    max_delay: float,
    tool_delay: float | None,
) -> None:
    """Run the continuous HTTP-only evaluation service against /api/eval/*."""
    _maybe_load_env_file(env_file)
    server, token, server_ca_cert = _resolve_auth(server, token, server_ca_cert)

    if slow_eval:
        if min_delay > max_delay:
            _handle_fatal_config_error("--min-delay cannot be greater than --max-delay")
        if concurrency != 1:
            logger.info("--slow-eval forces concurrency=1 (was %d)", concurrency)
            concurrency = 1

    actual_tool_delay = 7.0 if slow_eval else 0.0
    if tool_delay is not None:
        actual_tool_delay = tool_delay

    missing_auth = [
        name
        for name, value in (
            ("DASHBOARD_URL", server),
            ("EVAL_API_TOKEN", token),
        )
        if not value
    ]
    if missing_auth:
        _handle_fatal_config_error(
            f"Missing required environment variable(s): {', '.join(missing_auth)}. "
            "Please provide --token/--server or set CRAFT_EVAL_TOKEN / CRAFT_DASHBOARD_URL."
        )

    if issue and not project:
        _handle_fatal_config_error("--issue requires --project")

    if server_ca_cert:
        expanded_server_ca = pathlib.Path(server_ca_cert).expanduser()
        if not expanded_server_ca.is_file():
            _handle_fatal_config_error(
                f"Dashboard CA certificate file not found: '{server_ca_cert}' (resolved to '{expanded_server_ca}'). "
                "Check DASHBOARD_CA_CERT configuration."
            )

    llm_backend = (llm_backend or "").lower()
    ca_cert = ""
    llm_url = ""
    llm_key = ""
    if llm_backend == "copilot-acp" or (
        not llm_backend and os.environ.get("COPILOT_ACP_MODEL")
    ):
        llm_backend = "copilot-acp"
        model = os.environ.get("COPILOT_ACP_MODEL", "")
        if not model:
            _handle_fatal_config_error(
                "Missing required environment variable: COPILOT_ACP_MODEL. "
                "Set it in your environment (for example COPILOT_ACP_MODEL=gemini-3.8-flash)."
            )
        model_summary = model_scoring = model
    else:
        llm_url = os.environ.get("LLM_BASE_URL", "")
        llm_key = os.environ.get("LLM_API_KEY", "")
        single_model = os.environ.get("LLM_MODEL", "")
        model_summary = os.environ.get("LLM_MODEL_SUMMARY", single_model)
        model_scoring = os.environ.get("LLM_MODEL_SCORING", single_model)

        missing = []
        if not llm_url:
            missing.append("LLM_BASE_URL")
        if not llm_key:
            missing.append("LLM_API_KEY")
        if not model_summary and not model_scoring:
            missing.append("LLM_MODEL")
        elif not model_summary:
            missing.append("LLM_MODEL_SUMMARY")
        elif not model_scoring:
            missing.append("LLM_MODEL_SCORING")

        if missing:
            _handle_fatal_config_error(
                f"Missing required environment variable(s): {', '.join(missing)}."
            )

        ca_cert = os.environ.get("LLM_CA_CERT", "")
        if ca_cert:
            expanded_ca = pathlib.Path(ca_cert).expanduser()
            if not expanded_ca.is_file():
                _handle_fatal_config_error(
                    f"LLM CA certificate file not found: '{ca_cert}' (resolved to '{expanded_ca}'). "
                    "Check LLM_CA_CERT configuration."
                )

        llm_backend = "openrouter" if "openrouter.ai" in llm_url.lower() else "local"

    openrouter_key = llm_key if llm_backend == "openrouter" else ""

    asyncio.run(
        run_evaluate_loop(
            server=server,
            token=token,
            model_summary=model_summary,
            model_scoring=model_scoring,
            llm_backend=llm_backend,
            llm_url=llm_url,
            llm_api_key=llm_key,
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
            openrouter_api_key=openrouter_key,
            issue=issue,
            concurrency=concurrency,
            log=log,
            slow_eval=slow_eval,
            min_delay=min_delay,
            max_delay=max_delay,
            tool_delay=actual_tool_delay,
        )
    )


_evaluate_alias = copy.copy(evaluate_cmd)
_evaluate_alias.hidden = True
cli.add_command(_evaluate_alias, name="evaluate")
