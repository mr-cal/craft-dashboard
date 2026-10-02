"""Continuous HTTP-only evaluation worker for craft-dashboard.

This module is the entry point and orchestrator. The pieces it coordinates
live alongside it:

- :mod:`scripts.llm.worker_runtime` — process-wide pause/shutdown flags, the
  per-run counters, and the dependency bundle handed to each coroutine.
- :mod:`scripts.llm.eval_http` — the ``/api/eval/*`` request wrappers.
- :mod:`scripts.llm.eval_payload` — claim/result parsing and payload building.
- :mod:`scripts.llm.eval_failures` — classification of evaluation errors.
- :mod:`scripts.llm.eval_startup` — one-time setup before polling begins.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from craft_dashboard.config import load_config
from craft_dashboard.git_mirrors.paths import (
    UnknownProjectError,
    clone_url_for,
    resolve_allowed_projects,
)
from craft_dashboard.git_mirrors.sync import sync_mirror
from craft_dashboard.llm.evaluator import (
    EvaluationDiscarded,  # noqa: F401 - re-exported for callers and tests
    IssueEvaluator,
)
from craft_dashboard.llm.exceptions import LLMQuotaError
from craft_dashboard.llm.preflight import run_preflight
from craft_dashboard.settings import Settings
from rich.console import Console

from scripts.eval_timing import PHASE_EVALUATE, TimingHistory
from scripts.llm.console import format_elapsed, make_progress, setup_rich_logging
from scripts.llm.eval_failures import classify_evaluation_error
from scripts.llm.eval_http import (
    HTTP_CONFLICT,
    HTTP_NO_CONTENT,
    HTTP_OK,
    HTTP_TOO_MANY,
    fetch_project_orgs,
    format_error_body,
    log_queue_status,
    post_submission,
    release_claim,
    report_quota_pause,
)
from scripts.llm.eval_payload import (
    build_submission,
    build_tool_context,
    days_since,
    format_issue_label,
    issue_ref_for,
    validate_result,
    warn_on_hash_mismatch,
)
from scripts.llm.eval_startup import (
    build_queue_params,
    create_llm_client_for_backend,
    describe_filters,
    resolve_server_verify,
)
from scripts.llm.validation import LLMValidationError
from scripts.llm.worker_runtime import (
    RunState,
    Runtime,
    paused_state,
    release_and_maybe_stop,
    shutdown_state,
    signal_handler,
    start_keyboard_monitor,
    timed,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from craft_dashboard.llm.client import LLMClient

logger = logging.getLogger(__name__)

# Names re-exported under their historical spellings so the module's public
# surface (and the seams tests patch) stays exactly where callers expect it.
_RunState = RunState
_Runtime = Runtime
_timed = timed
_release_and_maybe_stop = release_and_maybe_stop
_signal_handler = signal_handler
_start_keyboard_monitor = start_keyboard_monitor
_format_error_body = format_error_body
_post_submission = post_submission
_release_claim = release_claim
_setup_logging = setup_rich_logging
_format_elapsed = format_elapsed
_make_progress = make_progress

_quota_pause_lock = asyncio.Lock()
_quota_paused = False


async def _sleep_until_next_poll(seconds: float) -> None:
    remaining = float(seconds)
    while remaining > 0 and not shutdown_state["requested"]:
        step = min(0.5, remaining)
        await asyncio.sleep(step)
        remaining -= step


#: How long to pause every worker after an LLM quota/payment error, before
#: trying again. Deliberately short (not "until tomorrow") since quota
#: providers can also free up mid-day (e.g. a shared/team pool), and a
#: crash-free 30-minute retry is cheap insurance either way.
_QUOTA_BACKOFF_SECONDS = 30 * 60


async def _enter_quota_backoff(runtime: _Runtime) -> None:
    """Pause every worker for `_QUOTA_BACKOFF_SECONDS` after a quota error.

    Without this, each worker that hits ``LLMQuotaError`` immediately loops
    back through ``_worker_loop`` and retries the very next issue, since
    neither ``_evaluate_issue``'s generic ``except Exception`` blocks nor
    ``_worker_loop`` itself impose any backoff for this error — resulting in
    a tight crash loop that hammers OpenRouter and the database with
    thousands of doomed requests per hour.

    Idempotent across concurrent workers: only the first caller actually
    logs, reports, and sleeps; later callers (or the same worker on its next
    attempt) see ``_quota_paused`` already set and return immediately,
    relying on ``paused_state`` (checked by every worker's loop) to keep
    them idle.
    """
    global _quota_paused  # noqa: PLW0603
    async with _quota_pause_lock:
        if _quota_paused:
            return
        _quota_paused = True

    paused_state["paused"] = True
    resume_at = datetime.now(tz=UTC) + timedelta(seconds=_QUOTA_BACKOFF_SECONDS)
    logger.error(
        "LLM quota exhausted. Pausing all workers for %s.",
        _format_elapsed(_QUOTA_BACKOFF_SECONDS),
    )
    await report_quota_pause(runtime, resume_at=resume_at)

    while not shutdown_state["requested"]:
        await _sleep_until_next_poll(_QUOTA_BACKOFF_SECONDS)
        if shutdown_state["requested"]:
            break
        client_to_check = getattr(runtime, "client", None)
        if client_to_check is not None and hasattr(client_to_check, "check_quota"):
            try:
                await client_to_check.check_quota()
                break
            except LLMQuotaError:
                logger.warning(
                    "LLM quota still exhausted after backoff; pausing for another %s.",
                    _format_elapsed(_QUOTA_BACKOFF_SECONDS),
                )
                resume_at = datetime.now(tz=UTC) + timedelta(
                    seconds=_QUOTA_BACKOFF_SECONDS
                )
                await report_quota_pause(runtime, resume_at=resume_at)
        else:
            break

    paused_state["paused"] = False
    async with _quota_pause_lock:
        _quota_paused = False
    if not shutdown_state["requested"]:
        logger.info("Quota backoff elapsed, resuming evaluation.")


#: Delay before a worker re-polls after an individual evaluation failure.
_FAILURE_BACKOFF_SECONDS = 5.0

#: Number of consecutive evaluation failures across all workers before tripping the circuit breaker.
_CONSECUTIVE_FAILURE_THRESHOLD = 5

#: Duration to pause all workers when the circuit breaker trips.
_CIRCUIT_BREAKER_PAUSE_SECONDS = 60.0

_circuit_breaker_lock = asyncio.Lock()
_circuit_breaker_active = False


async def _enter_circuit_breaker(runtime: _Runtime, failures: int) -> None:
    """Pause all workers for `_CIRCUIT_BREAKER_PAUSE_SECONDS` after consecutive failures."""
    global _circuit_breaker_active  # noqa: PLW0603
    async with _circuit_breaker_lock:
        if _circuit_breaker_active:
            return
        _circuit_breaker_active = True

    paused_state["paused"] = True
    logger.error(
        "Circuit breaker tripped: %d consecutive evaluation failures. "
        "Pausing all workers for %s...",
        failures,
        _format_elapsed(_CIRCUIT_BREAKER_PAUSE_SECONDS),
    )
    runtime.progress.update(
        runtime.overall_id,
        description=f"[red]Circuit breaker: paused for {_format_elapsed(_CIRCUIT_BREAKER_PAUSE_SECONDS)}…[/red]",
    )

    await _sleep_until_next_poll(_CIRCUIT_BREAKER_PAUSE_SECONDS)

    if hasattr(runtime.state, "consecutive_failures"):
        async with runtime.state.lock:
            runtime.state.consecutive_failures = 0

    paused_state["paused"] = False
    async with _circuit_breaker_lock:
        _circuit_breaker_active = False
    if not shutdown_state["requested"]:
        logger.info("Circuit breaker pause elapsed, resuming evaluation.")


async def _handle_evaluation_failure(runtime: _Runtime) -> None:
    """Record an evaluation failure and trip the circuit breaker if threshold reached."""
    if hasattr(runtime.state, "record_failure"):
        failures = await runtime.state.record_failure()
    else:
        return
    if failures >= _CONSECUTIVE_FAILURE_THRESHOLD:
        await _enter_circuit_breaker(runtime, failures)


async def _fetch_next_issue(
    runtime: _Runtime, *, server_url: str
) -> dict[str, Any] | None:
    """Claim the next issue from the HTTP queue, or return ``None`` on backoff."""
    response: httpx.Response | None = None
    sleep_seconds: int | None = None
    try:
        response = await runtime.http_client.get(
            "/api/eval/next",
            params=runtime.params,
            headers=runtime.headers,
        )
    except httpx.ConnectError as exc:
        hint = (
            " (server may not be using TLS — try http:// instead of https://)"
            if "SSL" in str(exc) or "wrong version" in str(exc).lower()
            else ""
        )
        logger.error("Cannot connect to %s%s", server_url, hint)  # noqa: TRY400
        sleep_seconds = runtime.poll_interval
    except httpx.HTTPStatusError as exc:
        logger.error(  # noqa: TRY400
            "HTTP error fetching work from %s: %s %s",
            server_url,
            exc.response.status_code,
            exc.response.text[:200],
        )
        sleep_seconds = runtime.poll_interval
    except httpx.HTTPError as exc:
        logger.error(  # noqa: TRY400
            "HTTP error fetching work from %s: %s: %s",
            server_url,
            type(exc).__name__,
            exc,
        )
        sleep_seconds = runtime.poll_interval

    if response is None:
        pass
    elif response.status_code == HTTP_NO_CONTENT:
        logger.info("No work available, polling in %ds", runtime.poll_interval)
        sleep_seconds = runtime.poll_interval
    elif response.status_code == HTTP_TOO_MANY:
        logger.warning(
            "Rate limited by server, backing off %ds", runtime.poll_interval * 6
        )
        sleep_seconds = runtime.poll_interval * 6
    elif response.status_code != HTTP_OK:
        logger.error(
            "Server returned %d from %s: %s",
            response.status_code,
            response.url,
            _format_error_body(response),
        )
        sleep_seconds = runtime.poll_interval
    else:
        try:
            return response.json()
        except ValueError:
            logger.error(  # noqa: TRY400
                "Server returned invalid JSON: %s",
                _format_error_body(response),
            )
            sleep_seconds = runtime.poll_interval

    if sleep_seconds is None:
        logger.error(
            "Server returned no response while fetching work from %s",
            server_url,
        )
        sleep_seconds = runtime.poll_interval

    await runtime.state.release()
    await _sleep_until_next_poll(sleep_seconds)
    return None


def _install_eval_progress(runtime: _Runtime, *, issue_ref: str) -> None:
    """Route the LLM client's retry notifications into the progress bar."""

    def _on_eval_attempt(attempt: int, total: int, *, _ref: str = issue_ref) -> None:
        runtime.progress.update(
            runtime.overall_id,
            description=f"[dim]{_ref}:[/dim] eval… ({attempt}/{total} attempts)",
        )

    runtime.client.retry_callback = _on_eval_attempt


def _start_evaluation(
    runtime: _Runtime,
    *,
    issue_data: dict[str, Any],
    state: str,
    tool_ctx: object,
) -> Awaitable[dict[str, Any] | None]:
    """Return the evaluator coroutine for one claimed issue."""
    author = issue_data.get("author") or ""
    maintainers = set(issue_data.get("maintainers", []))
    return runtime.evaluator.evaluate(
        title=issue_data["title"],
        body=issue_data.get("body"),
        issue_type=issue_data["issue_type"],
        state=state,
        labels=issue_data.get("labels", []),
        age_days=days_since(issue_data.get("created_at")),
        last_activity_days=days_since(issue_data.get("updated_at")),
        author=author,
        is_maintainer=author in maintainers
        or issue_data.get("author_association") == "MAINTAINER",
        comment_count=len(issue_data.get("comments", [])),
        comments=issue_data.get("comments"),
        closing_references=issue_data.get("closing_references"),
        pr_details=issue_data.get("pr_details"),
        project=issue_data["project_name"],
        external_id=issue_data.get("external_id"),
        tool_ctx=tool_ctx,
    )


async def _submit_evaluation(
    runtime: _Runtime,
    *,
    issue_ref: str,
    worker_name: str,
    submission: dict[str, Any],
) -> tuple[bool | None, str]:
    """POST a finished evaluation and map the response to an outcome.

    Returns ``(None, "")`` when the evaluation was stored and the caller
    should continue. Otherwise the first element is the value
    ``_evaluate_issue`` must return and the second is the release reason to
    record for the still-held claim.
    """
    runtime.progress.update(
        runtime.overall_id,
        description=f"[dim]{issue_ref} ({worker_name}):[/dim] posting eval…",
    )
    submit_response = await _post_submission(
        runtime,
        issue_ref=issue_ref,
        submission=submission,
    )
    if submit_response is None:
        await _release_and_maybe_stop(runtime)
        await _handle_evaluation_failure(runtime)
        return False, "submit_network_error"

    if submit_response.status_code == HTTP_CONFLICT:
        logger.warning("%s: content changed during evaluation, skipped", issue_ref)
        await _release_and_maybe_stop(runtime)
        return True, "content_changed"

    if submit_response.status_code != HTTP_OK:
        logger.error(
            "%s: submit failed %d from %s: %s",
            issue_ref,
            submit_response.status_code,
            submit_response.url,
            _format_error_body(submit_response),
        )
        await _release_and_maybe_stop(runtime)
        await _handle_evaluation_failure(runtime)
        return False, f"submit_failed_{submit_response.status_code}"

    return None, ""


async def _record_completion(
    runtime: _Runtime,
    *,
    issue_data: dict[str, Any],
    issue_ref: str,
    submission: dict[str, Any],
    started_at: float,
    evaluate_elapsed: float,
) -> None:
    """Advance the run counters and print the one-line result summary."""
    completed = await runtime.state.complete(
        prompt_tokens=submission["prompt_tokens"],
        completion_tokens=submission["completion_tokens"],
    )
    runtime.timing.add(PHASE_EVALUATE, time.monotonic() - started_at)
    runtime.progress.update(
        runtime.overall_id, advance=1, description="Evaluating issues"
    )
    action = submission.get("suggested_action") or "summary_only"
    issue_label = format_issue_label(
        issue_data,
        issue_ref=issue_ref,
        eval_server_base_url=runtime.eval_server_base_url,
    )

    runtime.progress.console.print(
        f"{issue_label} — {action}"
        f"  [dim]{submission['prompt_tokens']} in / {submission['completion_tokens']} out"
        f"  eval {_format_elapsed(evaluate_elapsed)}[/dim]"
    )

    if runtime.issue_limit > 0 and completed >= runtime.issue_limit:
        logger.info("Done: evaluated %d issues", runtime.issue_limit)
        shutdown_state["requested"] = True


async def _evaluate_issue(
    runtime: _Runtime,
    *,
    issue_data: dict[str, Any],
    worker_name: str,
) -> bool:
    """Evaluate one claimed issue, embed the summary, and submit the result."""
    warn_on_hash_mismatch(issue_data)

    issue_ref = issue_ref_for(issue_data)
    normalized_state = issue_data["state"].lower()

    runtime.progress.update(
        runtime.overall_id,
        description=f"[dim]{issue_ref} ({worker_name}):[/dim] eval…",
    )
    _install_eval_progress(runtime, issue_ref=issue_ref)

    started_at = time.monotonic()
    tool_ctx = build_tool_context(
        issue_data,
        mirror_dir=runtime.mirror_dir,
        allowed_projects=runtime.allowed_projects,
        eval_server_base_url=runtime.eval_server_base_url,
        headers=runtime.headers,
    )

    submitted = False
    release_reason = "evaluation_incomplete"
    issue_id = issue_data["issue_id"]

    try:
        try:
            result, evaluate_elapsed = await _timed(
                _start_evaluation(
                    runtime,
                    issue_data=issue_data,
                    state=normalized_state,
                    tool_ctx=tool_ctx,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad issue must not stop the worker
            runtime.progress.update(runtime.overall_id, description="Evaluating issues")
            failure = classify_evaluation_error(
                exc,
                issue_ref=issue_ref,
                elapsed=time.monotonic() - started_at,
            )
            release_reason = failure.release_reason
            if failure.is_quota:
                await runtime.state.release()
                await _enter_quota_backoff(runtime)
            else:
                await _release_and_maybe_stop(runtime)
                await _handle_evaluation_failure(runtime)
            return False

        if result is None:
            logger.warning("%s: content unchanged, skipping", issue_ref)
            release_reason = "content_unchanged"
            await _release_and_maybe_stop(runtime)
            return True

        try:
            validate_result(result, issue_data=issue_data, state=normalized_state)
        except LLMValidationError as exc:
            runtime.progress.update(runtime.overall_id, description="Evaluating issues")
            logger.warning(
                "%s: evaluation discarded (validation failed: %s); "
                "releasing claim, submitting nothing",
                issue_ref,
                exc,
            )
            release_reason = "evaluation_discarded"
            await _release_and_maybe_stop(runtime)
            await _handle_evaluation_failure(runtime)
            return False

        submission = build_submission(
            result,
            issue_data=issue_data,
            model=runtime.model,
            llm_backend=runtime.llm_backend,
        )

        outcome, reason = await _submit_evaluation(
            runtime,
            issue_ref=issue_ref,
            worker_name=worker_name,
            submission=submission,
        )
        if outcome is not None:
            release_reason = reason
            return outcome

        submitted = True
    finally:
        if not submitted:
            await _release_claim(
                runtime,
                issue_id=issue_id,
                issue_ref=issue_ref,
                reason=release_reason,
            )

    await _record_completion(
        runtime,
        issue_data=issue_data,
        issue_ref=issue_ref,
        submission=submission,
        started_at=started_at,
        evaluate_elapsed=evaluate_elapsed,
    )
    return True


async def _run_issue_preflight(
    runtime: _Runtime,
    *,
    issue_data: dict[str, Any],
    worker_name: str,
) -> bool:
    """Return whether a claimed issue passed preflight."""
    issue_ref = issue_ref_for(issue_data)
    runtime.progress.update(
        runtime.overall_id,
        description=f"[dim]{issue_ref} ({worker_name}):[/dim] preflight…",
    )

    async def _sync_claimed_repo(project: str) -> bool:
        try:
            clone_url = clone_url_for(
                project, allowed_projects=runtime.allowed_projects
            )
        except UnknownProjectError:
            logger.warning("%s: no clone URL available for %s", issue_ref, project)
            return False

        result = await sync_mirror(
            project,
            clone_url=clone_url,
            mirror_dir=runtime.mirror_dir,
        )
        return result.status != "skipped"

    async def _release_claim_preflight(*, issue_id: int, reason: str) -> None:
        await _release_claim(
            runtime,
            issue_id=issue_id,
            issue_ref=issue_ref,
            reason=reason,
        )

    async def _check_related_endpoint() -> bool:
        query = (issue_data.get("title") or issue_ref)[:1000]
        response = await runtime.http_client.get(
            "/api/eval/related",
            params={"issue_id": issue_data["issue_id"], "query": query},
            headers=runtime.headers,
        )
        return response.status_code == HTTP_OK

    result = await run_preflight(
        claim=issue_data,
        mirror_dir=runtime.mirror_dir,
        llm=runtime.client,
        sync_mirror=_sync_claimed_repo,
        release_claim=_release_claim_preflight,
        check_related_endpoint=_check_related_endpoint,
    )
    if result.ok:
        return True

    logger.warning(
        "%s: preflight blocked (%s); released claim before any LLM call",
        issue_ref,
        result.reason,
    )
    runtime.progress.update(runtime.overall_id, description="Evaluating issues")
    await _release_and_maybe_stop(runtime)
    # A released claim is immediately re-offered by ``/next``, so without a
    # backoff here a persistent preflight failure (e.g. the embedding
    # endpoint being down or rate/budget-limited) turns into a tight
    # claim/release retry loop across all workers, pinning the CPU. Back off
    # like the "no work available" path does.
    await _sleep_until_next_poll(runtime.poll_interval)
    return False


async def _pace_after_success(runtime: _Runtime) -> None:
    """Sleep a randomised pacing delay between evaluations in slow-eval mode."""
    if (
        runtime.slow_eval
        and not shutdown_state["requested"]
        and (runtime.issue_limit <= 0 or runtime.state.evaluated < runtime.issue_limit)
    ):
        delay = secrets.SystemRandom().uniform(runtime.min_delay, runtime.max_delay)
        runtime.progress.update(
            runtime.overall_id,
            description=f"[dim]Pacing delay: sleeping {delay:.1f}s…[/dim]",
        )
        await _sleep_until_next_poll(delay)


async def _worker_loop(
    runtime: _Runtime, *, server_url: str, worker_index: int
) -> None:
    """Run one worker coroutine until shutdown or the run limit is reached."""
    worker_name = f"worker-{worker_index}"
    while not shutdown_state["requested"]:
        if paused_state["paused"]:
            await asyncio.sleep(0.5)
            continue
        if not await runtime.state.reserve():
            return

        runtime.progress.update(
            runtime.overall_id, description=f"{worker_name}: getting issue…"
        )
        issue_data = await _fetch_next_issue(runtime, server_url=server_url)
        if issue_data is None:
            continue
        if not await _run_issue_preflight(
            runtime,
            issue_data=issue_data,
            worker_name=worker_name,
        ):
            continue
        success = await _evaluate_issue(
            runtime, issue_data=issue_data, worker_name=worker_name
        )
        if not success:
            if not shutdown_state["requested"]:
                await _sleep_until_next_poll(_FAILURE_BACKOFF_SECONDS)
            continue
        await _pace_after_success(runtime)


def _reset_process_state() -> None:
    """Clear the module-level pause/shutdown flags before a fresh run."""
    global _circuit_breaker_active, _quota_paused  # noqa: PLW0603
    shutdown_state["requested"] = False
    paused_state["paused"] = False
    _quota_paused = False
    _circuit_breaker_active = False


async def _await_startup_quota(
    http_client: httpx.AsyncClient,
    llm_client: LLMClient,
    *,
    headers: dict[str, str],
    issue: str,
    limit: int,
) -> bool:
    """Block until the LLM has quota. Returns False if the run should abort."""
    while not shutdown_state["requested"] and hasattr(llm_client, "check_quota"):
        try:
            await llm_client.check_quota()
            break
        except LLMQuotaError:
            logger.exception("LLM quota check failed")
            resume_at = datetime.now(tz=UTC) + timedelta(seconds=_QUOTA_BACKOFF_SECONDS)
            with contextlib.suppress(httpx.HTTPError):
                await http_client.post(
                    "/api/eval/quota-pause",
                    json={"resume_at": resume_at.isoformat(), "reason": "quota"},
                    headers=headers,
                )
            if issue or limit > 0:
                return False
            logger.info(
                "Pausing evaluation for %s due to quota exhaustion...",
                _format_elapsed(_QUOTA_BACKOFF_SECONDS),
            )
            paused_state["paused"] = True
            await _sleep_until_next_poll(_QUOTA_BACKOFF_SECONDS)
            paused_state["paused"] = False
    return True


def _log_run_totals(state: _RunState) -> None:
    """Log the token totals for a finished run, if anything was evaluated."""
    if state.evaluated > 0:
        logger.info(
            "Run total: %d issues, %d in / %d out tokens (%d total)",
            state.evaluated,
            state.total_prompt_tokens,
            state.total_completion_tokens,
            state.total_prompt_tokens + state.total_completion_tokens,
        )


async def run_evaluate_loop(
    *,
    server: str,
    token: str,
    model_summary: str,
    model_scoring: str,
    llm_backend: str,
    llm_url: str,
    llm_api_key: str,
    ca_cert: str,
    poll_interval: int,
    limit: int,
    project: str,
    open_only: bool,
    force: bool,
    incomplete: bool,
    stale_days: int,
    server_ca_cert: str,
    verbose: bool,
    openrouter_api_key: str,
    issue: str = "",
    concurrency: int = 10,
    log: bool = False,
    slow_eval: bool = False,
    min_delay: float = 25.0,
    max_delay: float = 55.0,
    tool_delay: float = 0.0,
) -> None:
    """Run the continuous HTTP evaluation worker against ``/api/eval/*``.

    Each worker coroutine independently polls ``GET /api/eval/next``, evaluates
    the claimed issue via the selected chat backend, and submits the finished
    payload to ``POST /api/eval/result``. No direct database access is used.
    """
    _reset_process_state()
    signal.signal(signal.SIGINT, _signal_handler)
    console = Console()
    log_file = _setup_logging(verbose=verbose, console=console, log=log)
    if log_file:
        logger.info("Logging detailed output to %s", log_file)

    server_url = server.rstrip("/")
    verify = resolve_server_verify(server_ca_cert)
    headers = {"Authorization": "Bearer " + token}
    params = build_queue_params(
        project=project,
        open_only=open_only,
        force=force,
        incomplete=incomplete,
        stale_days=stale_days,
        issue=issue,
    )
    if issue:
        limit = 1
    settings = Settings()
    config = load_config(settings.config_path)

    llm_timeout = float(os.environ.get("LLM_TIMEOUT", "600.0"))
    llm_client = create_llm_client_for_backend(
        llm_backend=llm_backend,
        openrouter_api_key=openrouter_api_key,
        llm_url=llm_url,
        llm_api_key=llm_api_key,
        ca_cert=ca_cert,
        timeout=llm_timeout,
    )
    evaluator = IssueEvaluator(
        client=llm_client,
        model_summary=model_summary,
        model_scoring=model_scoring,
        tool_call_delay=tool_delay,
    )

    filter_desc = describe_filters(project=project, open_only=open_only)

    try:
        async with httpx.AsyncClient(
            base_url=server_url, timeout=30.0, verify=verify
        ) as http_client:
            allowed_projects = resolve_allowed_projects(
                craft_projects=config.craft_projects,
                project_orgs=await fetch_project_orgs(http_client, headers=headers),
            )

            total_remaining = await log_queue_status(
                http_client,
                headers=headers,
                params=params,
                server_url=server_url,
                filter_desc=filter_desc,
                llm_backend=llm_backend,
                model_scoring=model_scoring,
                concurrency=concurrency,
                limit=limit,
            )

            if not await _await_startup_quota(
                http_client,
                llm_client,
                headers=headers,
                issue=issue,
                limit=limit,
            ):
                return

            if shutdown_state["requested"]:
                return

            _start_keyboard_monitor()
            if sys.stdin.isatty():
                logger.info("Press space to pause/unpause")

            task_total = total_remaining if total_remaining > 0 else None
            timing = TimingHistory()
            progress = _make_progress(
                console,
                task_total,
                timing,
                PHASE_EVALUATE,
                concurrency=concurrency,
            )
            state = _RunState(limit=limit)
            with progress:
                overall_id = progress.add_task("Evaluating issues", total=task_total)
                runtime = _Runtime(
                    client=llm_client,
                    evaluator=evaluator,
                    http_client=http_client,
                    headers=headers,
                    params=params,
                    progress=progress,
                    overall_id=overall_id,
                    timing=timing,
                    state=state,
                    poll_interval=poll_interval,
                    issue_limit=limit,
                    model=model_scoring,
                    llm_backend=llm_backend,
                    mirror_dir=settings.mirror_dir_path,
                    allowed_projects=allowed_projects,
                    eval_server_base_url=server_url,
                    slow_eval=slow_eval,
                    min_delay=min_delay,
                    max_delay=max_delay,
                )
                await asyncio.gather(
                    *(
                        _worker_loop(runtime, server_url=server_url, worker_index=index)
                        for index in range(1, concurrency + 1)
                    )
                )

                _log_run_totals(state)
    finally:
        await llm_client.close()
