#!/usr/bin/env python3
"""Pull-based LLM evaluation client for craft-dashboard."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import select
import signal
import sys
import termios
import threading
import time
import tty
from datetime import UTC, datetime
from typing import Any

import click
import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.llm.client import LocalLLMClient
from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.llm.evaluator import IssueEvaluator, _compute_content_hash

from scripts.eval_timing import PHASE_EVALUATE, TimingHistory

logger = logging.getLogger(__name__)

HTTP_OK = httpx.codes.OK
HTTP_NO_CONTENT = httpx.codes.NO_CONTENT
HTTP_CONFLICT = httpx.codes.CONFLICT
shutdown_state = {"requested": False}
paused_state = {"paused": False}

_MAX_ERROR_BODY = 200


def _setup_logging(*, verbose: bool, console: Console) -> None:
    """Configure logging with a RichHandler backed by *console*."""
    handler = RichHandler(
        console=console,
        show_path=False,
        show_time=verbose,
        markup=False,
        rich_tracebacks=False,
        log_time_format="%H:%M:%S",
    )
    # basicConfig is a no-op when handlers already exist (e.g. in tests).
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(message)s")
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("craft_dashboard.llm.client").setLevel(logging.WARNING)


def _format_elapsed(seconds: float) -> str:
    """Format elapsed time as a human-readable string."""
    if seconds < 60:  # noqa: PLR2004
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    return f"{minutes}m{remaining:02d}s"


def _format_error_body(response: httpx.Response) -> str:
    """Return a compact, readable summary of a non-2xx response body."""
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            data = response.json()
            return str(data.get("detail", data))
        except ValueError:
            pass
    if "html" in content_type:
        # 4xx with HTML usually means the URL hit a different server (e.g. a
        # CDN, proxy, or wrong host) that doesn't serve the API.
        # 5xx with HTML means the craft-dashboard server itself errored —
        # check the server logs for the root cause.
        if response.status_code >= 500:  # noqa: PLR2004
            return (
                f"(HTML response from {response.url} — "
                "server error; check the craft-dashboard logs)"
            )
        return f"(HTML response from {response.url} — is the server URL correct?)"
    text = response.text.strip()
    return (text[:_MAX_ERROR_BODY] + "…") if len(text) > _MAX_ERROR_BODY else text


def _make_progress(console: Console, total: int | None) -> Progress:  # noqa: ARG001
    """Create a rich Progress bar for the eval loop.

    Returns a Progress configured for two tasks:
    - Row 0 (status): spinner + description showing the current issue's phase status.
    - Row 1 (overall): full bar with count, percentage, ETA, and elapsed time.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TextColumn("[cyan]ETA: {task.fields[eta]}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


async def _embed_safe(
    embed_client: EmbeddingClient | None,
    embed_text: str,
    issue_ref: str,
) -> list[float] | None:
    """Compute an embedding, returning None and logging a warning on failure."""
    if embed_client is None:
        return None
    try:
        return await embed_client.embed(embed_text)
    except Exception as exc:
        logger.warning("%s: embedding failed: %s", issue_ref, exc)
        return None


def _signal_handler(signum, frame) -> None:
    del signum, frame
    shutdown_state["requested"] = True
    logger.info("Shutting down after current evaluation...")


def _start_keyboard_monitor() -> None:
    """Monitor stdin for space key to pause/unpause. No-op if not a TTY."""
    if not sys.stdin.isatty():
        return

    import contextlib

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


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _days_since(value: str | None) -> int:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return 0
    return max(0, (datetime.now(tz=UTC) - timestamp).days)


async def _sleep_until_next_poll(seconds: int) -> None:
    remaining = seconds
    while remaining > 0 and not shutdown_state["requested"]:
        await asyncio.sleep(min(1, remaining))
        remaining -= 1


async def run_eval_loop(  # noqa: PLR0913
    *,
    server: str,
    token: str,
    summary_model: str,
    evaluation_model: str,
    llm_url: str,
    llm_api_key: str,
    ca_cert: str,
    embed_model: str,
    poll_interval: int,
    limit: int,
    project: str,
    open_only: bool,
    force: bool,
    incomplete: bool,
    stale_days: int,
    server_ca_cert: str,
    verbose: bool,
) -> None:
    """Poll the eval API, run single-phase evaluation, and submit results.

    For each pending issue, the loop runs three steps in sequence:
      1. Summarize — calls evaluator.summarize() to produce a text summary.
      2. Score + Embed — runs evaluator.score() and EmbeddingClient.embed()
         concurrently via asyncio.gather().  Embed is skipped when embed_model
         is empty or the embedding call fails (non-fatal).
      3. Submit — POSTs the assembled payload to /api/eval/result.

    Closed/merged issues skip scoring and go straight to embed + submit.

    The loop runs until *limit* issues are evaluated (limit=0 means run until
    the server returns 204 No Content), or until a shutdown signal is received.
    """
    signal.signal(signal.SIGINT, _signal_handler)
    console = Console()
    _setup_logging(verbose=verbose, console=console)

    server_url = server.rstrip("/")
    llm_base_url = llm_url.rstrip("/")
    verify: bool | str = server_ca_cert if server_ca_cert else True
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "project": project,
        "open_only": open_only,
        "force": force,
        "incomplete": incomplete,
        "stale_days": stale_days,
    }

    llm_client = LocalLLMClient(
        base_url=llm_base_url,
        api_key=llm_api_key,
        ca_cert=ca_cert,
    )
    evaluator = IssueEvaluator(
        client=llm_client,
        summary_model=summary_model,
        evaluation_model=evaluation_model,
    )
    embed_client: EmbeddingClient | None = None
    if embed_model:
        embed_client = EmbeddingClient(
            base_url=llm_base_url,
            model=embed_model,
            api_key=llm_api_key,
            ca_cert=ca_cert,
        )
    timing = TimingHistory()
    filter_parts = []
    if project:
        filter_parts.append(project)
    if open_only:
        filter_parts.append("open only")
    filter_desc = f" ({', '.join(filter_parts)})" if filter_parts else ""

    try:
        async with httpx.AsyncClient(
            base_url=server_url,
            timeout=30.0,
            verify=verify,
        ) as http_client:
            # Fetch queue status at startup for progress display
            total_remaining = limit if limit > 0 else 0
            try:
                status_resp = await http_client.get(
                    "/api/eval/status", params=params, headers=headers
                )
                if status_resp.status_code == HTTP_OK:
                    status_data = status_resp.json()
                    server_pending = status_data.get("pending", 0)
                    total_open = status_data.get("total_open", 0)
                    total_evaluated = status_data.get("total_evaluated", 0)
                    already_pct = (
                        int(100 * total_evaluated / total_open) if total_open > 0 else 0
                    )
                    total_remaining = limit if limit > 0 else server_pending
                    logger.info(
                        "Connected to %s%s — %d/%d open issues evaluated (%d%%), "
                        "%d pending | model=%s",
                        server_url,
                        filter_desc,
                        total_evaluated,
                        total_open,
                        already_pct,
                        server_pending,
                        evaluation_model,
                    )
            except httpx.HTTPError:
                logger.info(
                    "Connected to %s%s, model=%s",
                    server_url,
                    filter_desc,
                    evaluation_model,
                )

            _start_keyboard_monitor()
            if sys.stdin.isatty():
                logger.info("Press space to pause/unpause")

            task_total = total_remaining if total_remaining > 0 else None
            progress = _make_progress(console, task_total)
            with progress:
                status_id = progress.add_task("", total=None, visible=False, eta="")
                overall_id = progress.add_task(
                    "Evaluating issues",
                    total=task_total,
                    eta=timing.eta(PHASE_EVALUATE, total_remaining),
                )

                evaluated = 0
                total_prompt_tokens = 0
                total_completion_tokens = 0
                while not shutdown_state["requested"]:
                    # Honour pause before fetching next issue
                    if paused_state["paused"]:
                        await asyncio.sleep(0.5)
                        continue

                    try:
                        response = await http_client.get(
                            "/api/eval/next",
                            params=params,
                            headers=headers,
                        )
                    except httpx.ConnectError as exc:
                        hint = (
                            " (server may not be using TLS — try http:// instead of https://)"
                            if "SSL" in str(exc) or "wrong version" in str(exc).lower()
                            else ""
                        )
                        logger.error("Cannot connect to %s%s", server_url, hint)  # noqa: TRY400
                        await _sleep_until_next_poll(poll_interval)
                        continue
                    except httpx.HTTPError as exc:
                        logger.error(  # noqa: TRY400
                            "HTTP error fetching work from %s: %s", server_url, exc
                        )
                        await _sleep_until_next_poll(poll_interval)
                        continue

                    if response.status_code == HTTP_NO_CONTENT:
                        logger.info("No work available, polling in %ds", poll_interval)
                        await _sleep_until_next_poll(poll_interval)
                        continue

                    if response.status_code != HTTP_OK:
                        logger.error(
                            "Server returned %d from %s: %s",
                            response.status_code,
                            response.url,
                            _format_error_body(response),
                        )
                        await _sleep_until_next_poll(poll_interval)
                        continue

                    try:
                        issue_data = response.json()
                    except ValueError:
                        logger.error(  # noqa: TRY400
                            "Server returned invalid JSON: %s",
                            _format_error_body(response),
                        )
                        await _sleep_until_next_poll(poll_interval)
                        continue

                    local_hash = _compute_content_hash(
                        issue_data["title"],
                        issue_data.get("body"),
                        issue_data["state"],
                        issue_data.get("labels", []),
                        issue_data.get("comments"),
                    )
                    current_hash = issue_data.get("current_hash", "")
                    if current_hash and local_hash != current_hash:
                        logger.warning(
                            "Issue %s: server hash mismatch (server=%s local=%s)",
                            issue_data["external_id"],
                            current_hash,
                            local_hash,
                        )

                    issue_ref = (
                        f"{issue_data['project_name']}#{issue_data['external_id']}"
                    )
                    author = issue_data.get("author") or ""
                    maintainers = set(issue_data.get("maintainers", []))
                    normalized_state = issue_data["state"].lower()

                    # Show live status line for this issue
                    progress.update(
                        status_id,
                        description=f"[dim]{issue_ref}:[/dim] summarize…",
                        visible=True,
                    )

                    t0 = time.monotonic()

                    # --- Step 1: Summarize ---
                    try:
                        (
                            summary,
                            summary_tokens,
                            summary_prompt,
                            summary_completion,
                        ) = await evaluator.summarize(
                            title=issue_data["title"],
                            body=issue_data.get("body"),
                            issue_type=issue_data["issue_type"],
                            state=normalized_state,
                            labels=issue_data.get("labels", []),
                            age_days=_days_since(issue_data.get("created_at")),
                            last_activity_days=_days_since(
                                issue_data.get("updated_at")
                            ),
                            author=author,
                            is_maintainer=author in maintainers
                            or issue_data.get("author_association") == "MAINTAINER",
                            comment_count=len(issue_data.get("comments", [])),
                            comments=issue_data.get("comments"),
                        )
                    except Exception:
                        progress.update(status_id, visible=False)
                        elapsed = _format_elapsed(time.monotonic() - t0)
                        logger.exception(
                            "%s: summarization failed after %s",
                            issue_ref,
                            elapsed,
                        )
                        continue

                    # Embed title + summary for richer signal.
                    # Title anchors the topic; summary captures semantic detail.
                    embed_text = f"{issue_data['title']}. {summary}"

                    # Helper: safely compute embedding, log and swallow failures

                    content_hash = _compute_content_hash(
                        issue_data["title"],
                        issue_data.get("body"),
                        normalized_state,
                        issue_data.get("labels", []),
                        issue_data.get("comments"),
                    )
                    embed_waiting = "[dim]—[/dim]" if embed_client is None else "⠋"

                    # --- Step 2a: Closed/merged — skip scoring ---
                    if normalized_state in {"closed", "merged"}:
                        progress.update(
                            status_id,
                            description=(
                                f"[dim]{issue_ref}:[/dim] summarize [green]✓[/green]"
                                f" | score [dim]—[/dim] | embed {embed_waiting}"
                            ),
                        )
                        summary_embedding = await _embed_safe(
                            embed_client, embed_text, issue_ref
                        )
                        duration = time.monotonic() - t0
                        elapsed = _format_elapsed(duration)
                        submission: dict[str, Any] = {
                            "issue_id": issue_data["issue_id"],
                            "content_hash": content_hash,
                            "summary": summary,
                            "scores": {},
                            "suggested_action": None,
                            "suggested_action_reason": None,
                            "tokens_used": summary_tokens,
                            "prompt_tokens": summary_prompt,
                            "completion_tokens": summary_completion,
                            "model_used": evaluation_model,
                            "llm_backend": "local",
                            "summary_embedding": summary_embedding,
                        }

                    # --- Step 2b: Open — score and embed in parallel ---
                    else:
                        progress.update(
                            status_id,
                            description=(
                                f"[dim]{issue_ref}:[/dim] summarize [green]✓[/green]"
                                f" | score ⠋ | embed {embed_waiting}"
                            ),
                        )
                        try:
                            (
                                (
                                    parsed,
                                    eval_tokens,
                                    eval_prompt,
                                    eval_completion,
                                ),
                                summary_embedding,
                            ) = await asyncio.gather(
                                evaluator.score(
                                    title=issue_data["title"],
                                    body=issue_data.get("body"),
                                    issue_type=issue_data["issue_type"],
                                    labels=issue_data.get("labels", []),
                                    age_days=_days_since(issue_data.get("created_at")),
                                    last_activity_days=_days_since(
                                        issue_data.get("updated_at")
                                    ),
                                    author=author,
                                    is_maintainer=author in maintainers
                                    or issue_data.get("author_association")
                                    == "MAINTAINER",
                                    comment_count=len(issue_data.get("comments", [])),
                                    comments=issue_data.get("comments"),
                                ),
                                _embed_safe(embed_client, embed_text, issue_ref),
                            )
                        except Exception:
                            progress.update(status_id, visible=False)
                            elapsed = _format_elapsed(time.monotonic() - t0)
                            logger.exception(
                                "%s: scoring failed after %s",
                                issue_ref,
                                elapsed,
                            )
                            continue

                        duration = time.monotonic() - t0
                        elapsed = _format_elapsed(duration)
                        scores = parsed.get("scores", {}) if parsed else {}
                        if "confidence" not in scores:
                            scores["confidence"] = 50

                        submission = {
                            "issue_id": issue_data["issue_id"],
                            "content_hash": content_hash,
                            "summary": summary,
                            "scores": scores,
                            "suggested_action": parsed.get("suggested_action")
                            if parsed
                            else None,
                            "suggested_action_reason": parsed.get(
                                "suggested_action_reason"
                            )
                            if parsed
                            else None,
                            "tokens_used": summary_tokens + eval_tokens,
                            "prompt_tokens": summary_prompt + eval_prompt,
                            "completion_tokens": summary_completion + eval_completion,
                            "model_used": evaluation_model,
                            "llm_backend": "local",
                            "summary_embedding": summary_embedding,
                        }

                    # Hide status row before submitting
                    progress.update(status_id, visible=False)

                    try:
                        submit_response = await http_client.post(
                            "/api/eval/result",
                            json=submission,
                            headers=headers,
                        )
                    except httpx.ConnectError:
                        logger.error(  # noqa: TRY400
                            "%s: lost connection to server while submitting",
                            issue_ref,
                        )
                        continue
                    except httpx.HTTPError as exc:
                        logger.error(  # noqa: TRY400
                            "%s: HTTP error submitting result: %s",
                            issue_ref,
                            exc,
                        )
                        continue

                    if submit_response.status_code == HTTP_CONFLICT:
                        logger.warning(
                            "%s: content changed during evaluation, skipped",
                            issue_ref,
                        )
                        continue

                    if submit_response.status_code != HTTP_OK:
                        logger.error(
                            "%s: submit failed %d from %s: %s",
                            issue_ref,
                            submit_response.status_code,
                            submit_response.url,
                            _format_error_body(submit_response),
                        )
                        continue

                    evaluated += 1
                    prompt_tok = submission["prompt_tokens"]
                    completion_tok = submission["completion_tokens"]
                    total_prompt_tokens += prompt_tok
                    total_completion_tokens += completion_tok
                    timing.add(PHASE_EVALUATE, duration)
                    remaining = max(0, (task_total or evaluated) - evaluated)
                    progress.update(
                        overall_id,
                        advance=1,
                        eta=timing.eta(PHASE_EVALUATE, remaining),
                    )
                    action = submission.get("suggested_action") or "summary_only"
                    progress.console.print(
                        f"[bold]{issue_ref}[/bold] — {action}"
                        f"  [dim]{prompt_tok} in / {completion_tok} out tokens  {elapsed}[/dim]"
                    )

                    if limit > 0 and evaluated >= limit:
                        logger.info("Done: evaluated %d issues", limit)
                        break

                if evaluated > 0:
                    logger.info(
                        "Run total: %d issues, %d in / %d out tokens (%d total)",
                        evaluated,
                        total_prompt_tokens,
                        total_completion_tokens,
                        total_prompt_tokens + total_completion_tokens,
                    )
    finally:
        await llm_client.close()
        if embed_client is not None:
            await embed_client.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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
    "--llm-url",
    default="http://localhost:11434/v1",
    show_default=True,
    envvar="LOCAL_LLM_URL",
    help="OpenAI-compatible LLM endpoint [env: LOCAL_LLM_URL]",
)
@click.option(
    "--llm-api-key",
    default="",
    envvar="LOCAL_LLM_API_KEY",
    help="API key for the LLM endpoint [env: LOCAL_LLM_API_KEY]",
)
@click.option(
    "--ca-cert",
    default="",
    envvar="LOCAL_LLM_CA_CERT",
    help="PEM CA cert path for LLM server TLS verification [env: LOCAL_LLM_CA_CERT]",
)
@click.option(
    "--server-ca-cert",
    default="",
    envvar="EVAL_CLIENT_SERVER_CA_CERT",
    help="PEM CA cert for verifying the server TLS cert [env: EVAL_CLIENT_SERVER_CA_CERT]",
)
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Show timestamps, URLs, and model details",
)
@click.option(
    "--summary-model",
    default="llama3.2",
    show_default=True,
    envvar="LOCAL_LLM_SUMMARY_MODEL",
    help="LLM model for summarization [env: LOCAL_LLM_SUMMARY_MODEL]",
)
@click.option(
    "--evaluation-model",
    default="llama3.2",
    show_default=True,
    envvar="LOCAL_LLM_EVALUATION_MODEL",
    help="LLM model for scoring [env: LOCAL_LLM_EVALUATION_MODEL]",
)
@click.option(
    "--embed-model",
    default="",
    envvar="LOCAL_LLM_EMBEDDING_MODEL",
    help="Embedding model for similarity search [env: LOCAL_LLM_EMBEDDING_MODEL]. "
    "Leave blank to skip embedding generation.",
)
@click.option(
    "--poll-interval",
    default=30,
    show_default=True,
    type=click.IntRange(min=1),
    help="Seconds between polls when no work is available",
)
@click.option(
    "--limit",
    default=0,
    show_default=True,
    type=click.IntRange(min=0),
    help="Max evaluations before exit (0=unlimited)",
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
def cli(  # noqa: PLR0913
    server: str,
    token: str,
    summary_model: str,
    evaluation_model: str,
    embed_model: str,
    llm_url: str,
    llm_api_key: str,
    ca_cert: str,
    server_ca_cert: str,
    poll_interval: int,
    limit: int,
    project: str,
    open_only: bool,
    force: bool,
    incomplete: bool,
    stale_days: int,
    verbose: bool,
) -> None:
    """craft-dashboard local evaluation client.

    Evaluates issues in a single pass: summarize, then score and embed in parallel.
    """
    asyncio.run(
        run_eval_loop(
            server=server,
            token=token,
            summary_model=summary_model,
            evaluation_model=evaluation_model,
            embed_model=embed_model,
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
        )
    )


if __name__ == "__main__":
    # Load .env from the repo root so dev machine settings are picked up automatically.
    # This is intentionally dev-only — production runners set env vars directly.
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
    cli()
