"""Continuous HTTP-only evaluation worker for craft-dashboard."""

from __future__ import annotations

import asyncio
import logging
import select
import signal
import sys
import termios
import threading
import time
import tty
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from craft_dashboard.config import load_config
from craft_dashboard.git_mirrors.paths import clone_url_for, resolve_allowed_projects
from craft_dashboard.git_mirrors.sync import sync_mirror
from craft_dashboard.llm.client import (
    OPENROUTER_BASE_URL,
    LocalLLMClient,
    OpenRouterClient,
)
from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.llm.evaluator import (
    EvaluationDiscarded,
    IssueEvaluator,
    _compute_content_hash,
)
from craft_dashboard.llm.exceptions import LLMQuotaError
from craft_dashboard.llm.preflight import run_preflight
from craft_dashboard.llm.tool_dispatch import ToolContext
from craft_dashboard.settings import Settings
from rich.console import Console

from scripts import backfill_search_embeddings
from scripts.eval_timing import PHASE_EVALUATE, TimingHistory
from scripts.llm.console import format_elapsed, make_progress, setup_rich_logging

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path

    from craft_dashboard.llm.client import LLMClient
    from rich.progress import Progress, TaskID

logger = logging.getLogger(__name__)

HTTP_OK = httpx.codes.OK
HTTP_NO_CONTENT = httpx.codes.NO_CONTENT
HTTP_CONFLICT = httpx.codes.CONFLICT
HTTP_TOO_MANY = httpx.codes.TOO_MANY_REQUESTS
shutdown_state = {"requested": False}
paused_state = {"paused": False}
_quota_pause_lock = asyncio.Lock()
_quota_paused = False

_MAX_ERROR_BODY = 200
_setup_logging = setup_rich_logging
_format_elapsed = format_elapsed
_make_progress = make_progress


async def _timed[T](coro: Awaitable[T]) -> tuple[T, float]:
    """Run an awaitable and return ``(result, elapsed_seconds)``."""
    started_at = time.monotonic()
    result = await coro
    return result, time.monotonic() - started_at


class _RunState:
    """Shared counters and limit reservation state for worker coroutines."""

    def __init__(self, *, limit: int) -> None:
        self.limit = limit
        self.reserved = 0
        self.evaluated = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
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

    async def complete(self, *, prompt_tokens: int, completion_tokens: int) -> int:
        """Record one successful evaluation and return the new total."""
        async with self.lock:
            self.evaluated += 1
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            return self.evaluated


class _Runtime:
    """Shared runtime dependencies for worker coroutines."""

    def __init__(
        self,
        *,
        client: LLMClient,
        evaluator: IssueEvaluator,
        embed_client: EmbeddingClient,
        http_client: httpx.AsyncClient,
        headers: dict[str, str],
        params: dict[str, Any],
        progress: Progress,
        overall_id: TaskID,
        timing: TimingHistory,
        state: _RunState,
        poll_interval: int,
        issue_limit: int,
        model: str,
        llm_backend: str,
        mirror_dir: Path,
        allowed_projects: dict[str, str],
        eval_server_base_url: str,
        single_issue: bool = False,
    ) -> None:
        self.client = client
        self.evaluator = evaluator
        self.embed_client = embed_client
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
        #: True for a single-target ``--issue ... --force`` run (e.g. the
        #: canary script). In that mode there is only ever one issue to
        #: evaluate, and a claimed-then-discarded/failed/skipped issue is
        #: never re-offered by ``/next`` (the server sees it as already
        #: attempted), so any terminal outcome — not just success — must
        #: end the run. Otherwise the worker polls "no work available"
        #: forever, bounded only by an external timeout.
        self.single_issue = single_issue


async def _release_and_maybe_stop(runtime: _Runtime) -> None:
    """Release a reserved slot after a terminal (non-retryable) outcome.

    In single-issue mode (see ``_Runtime.single_issue``) this also requests
    shutdown, since there is nothing else left to poll for: a
    claimed-then-discarded/failed/skipped issue is never re-offered by
    ``/next`` in a single-issue ``--force`` run, so without this the worker
    would poll "no work available" forever, bounded only by an external
    timeout.
    """
    await runtime.state.release()
    if runtime.single_issue:
        shutdown_state["requested"] = True


async def _embed_summary(
    embed_client: EmbeddingClient,
    *,
    title: str,
    summary: str,
) -> list[float]:
    """Compute the required summary embedding for a finished evaluation."""
    return await embed_client.embed(f"{title}. {summary}", dimensions=1024)


async def _embed_search_text(
    embed_client: EmbeddingClient,
    *,
    title: str,
    body: str | None,
) -> list[float]:
    """Compute the issue's search embedding (title+body) for semantic search.

    Uses the same text shape as ``scripts/backfill_search_embeddings.py``
    (``build_search_embedding_text``) so historical and ongoing embeddings
    live in the same vector space.
    """
    text = backfill_search_embeddings.build_search_embedding_text(title, body)
    return await embed_client.embed(text, dimensions=1024)


def create_llm_client_for_backend(
    *,
    llm_backend: str,
    openrouter_api_key: str,
    llm_url: str,
    llm_api_key: str,
    ca_cert: str,
) -> LLMClient:
    """Create the completion client for the selected backend."""
    if llm_backend == "openrouter":
        return OpenRouterClient(api_key=openrouter_api_key)
    if llm_backend == "local":
        return LocalLLMClient(
            base_url=llm_url.rstrip("/"),
            api_key=llm_api_key,
            ca_cert=ca_cert,
        )
    raise ValueError(f"Unsupported llm backend: {llm_backend}")


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
        if response.status_code >= 500:  # noqa: PLR2004
            return (
                f"(HTML response from {response.url} — "
                "server error; check the craft-dashboard logs)"
            )
        return f"(HTML response from {response.url} — is the server URL correct?)"
    text = response.text.strip()
    return (text[:_MAX_ERROR_BODY] + "…") if len(text) > _MAX_ERROR_BODY else text


def _serialize_evidence_paths(ctx: object | None) -> list[dict[str, str]]:
    """Return sorted worker-touched repo/path pairs for `/result` payloads."""
    touched_paths = getattr(ctx, "touched_paths", None) or set()
    return [{"repo": repo, "path": path} for repo, path in sorted(touched_paths)]


def _signal_handler(signum: int, frame: object) -> None:
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


#: How long to pause every worker after an LLM quota/payment error, before
#: trying again. Deliberately short (not "until tomorrow") since quota
#: providers can also free up mid-day (e.g. a shared/team pool), and a
#: crash-free 30-minute retry is cheap insurance either way.
_QUOTA_BACKOFF_SECONDS = 30 * 60


async def _report_quota_pause(runtime: _Runtime, *, resume_at: datetime) -> None:
    """Tell the server this worker is pausing for a quota backoff.

    Best-effort: if the request fails (e.g. the server is briefly
    unreachable), the worker still pauses locally — only the admin page's
    "why is it stalled" context is lost, not the backoff itself.
    """
    try:
        await runtime.http_client.post(
            "/api/eval/quota-pause",
            json={"resume_at": resume_at.isoformat(), "reason": "quota"},
            headers=runtime.headers,
        )
    except httpx.HTTPError:
        logger.warning("Could not report quota pause to server", exc_info=True)


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
    await _report_quota_pause(runtime, resume_at=resume_at)
    await _sleep_until_next_poll(_QUOTA_BACKOFF_SECONDS)
    paused_state["paused"] = False
    async with _quota_pause_lock:
        _quota_paused = False
    if not shutdown_state["requested"]:
        logger.info("Quota backoff elapsed, resuming evaluation.")


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


async def _post_submission(
    runtime: _Runtime,
    *,
    issue_ref: str,
    submission: dict[str, Any],
) -> httpx.Response | None:
    """POST an evaluation result back to the craft-dashboard API."""
    try:
        return await runtime.http_client.post(
            "/api/eval/result",
            json=submission,
            headers=runtime.headers,
        )
    except httpx.ConnectError:
        logger.error("%s: lost connection to server while submitting", issue_ref)  # noqa: TRY400
    except httpx.HTTPError as exc:
        logger.error("%s: HTTP error submitting result: %s", issue_ref, exc)  # noqa: TRY400
    return None


async def _evaluate_issue(  # noqa: PLR0911
    runtime: _Runtime,
    *,
    issue_data: dict[str, Any],
    worker_name: str,
) -> None:
    """Evaluate one claimed issue, embed the summary, and submit the result."""
    local_hash = _compute_content_hash(
        issue_data["title"],
        issue_data.get("body"),
        issue_data["state"],
        issue_data.get("labels", []),
        issue_data.get("comments"),
        pr_details=issue_data.get("pr_details"),
    )
    current_hash = issue_data.get("current_hash", "")
    if current_hash and local_hash != current_hash:
        logger.warning(
            "Issue %s: server hash mismatch (server=%s local=%s)",
            issue_data["external_id"],
            current_hash,
            local_hash,
        )

    issue_ref = f"{issue_data['project_name']}#{issue_data['external_id']}"
    author = issue_data.get("author") or ""
    maintainers = set(issue_data.get("maintainers", []))
    normalized_state = issue_data["state"].lower()

    runtime.progress.update(
        runtime.overall_id,
        description=f"[dim]{issue_ref} ({worker_name}):[/dim] eval…",
    )

    def _on_eval_attempt(attempt: int, total: int, *, _ref: str = issue_ref) -> None:
        runtime.progress.update(
            runtime.overall_id,
            description=f"[dim]{_ref}:[/dim] eval… ({attempt}/{total} attempts)",
        )

    runtime.client.retry_callback = _on_eval_attempt
    started_at = time.monotonic()
    project_name = issue_data["project_name"]
    tool_ctx = ToolContext(
        mirror_dir=runtime.mirror_dir,
        allowed_projects=runtime.allowed_projects,
        pinned_shas=issue_data.get("repo_shas", {}),
        eval_server_base_url=runtime.eval_server_base_url,
        eval_api_token=runtime.headers.get("Authorization", "").removeprefix("Bearer "),
        issue_id=issue_data["issue_id"],
        embed_client=runtime.embed_client,
    )

    try:
        result, evaluate_elapsed = await _timed(
            runtime.evaluator.evaluate(
                title=issue_data["title"],
                body=issue_data.get("body"),
                issue_type=issue_data["issue_type"],
                state=normalized_state,
                labels=issue_data.get("labels", []),
                age_days=_days_since(issue_data.get("created_at")),
                last_activity_days=_days_since(issue_data.get("updated_at")),
                author=author,
                is_maintainer=author in maintainers
                or issue_data.get("author_association") == "MAINTAINER",
                comment_count=len(issue_data.get("comments", [])),
                comments=issue_data.get("comments"),
                closing_references=issue_data.get("closing_references"),
                pr_details=issue_data.get("pr_details"),
                project=project_name,
                tool_ctx=tool_ctx,
            )
        )
    except EvaluationDiscarded as exc:
        runtime.progress.update(runtime.overall_id, description="Evaluating issues")
        logger.warning(
            "%s: evaluation discarded (post-preflight tool failure): %s; "
            "releasing claim, submitting nothing",
            issue_ref,
            exc,
        )
        await _release_and_maybe_stop(runtime)
        return
    except LLMQuotaError:
        runtime.progress.update(runtime.overall_id, description="Evaluating issues")
        await runtime.state.release()
        await _enter_quota_backoff(runtime)
        return
    except Exception:
        runtime.progress.update(runtime.overall_id, description="Evaluating issues")
        logger.exception(
            "%s: evaluation failed after %s",
            issue_ref,
            _format_elapsed(time.monotonic() - started_at),
        )
        await _release_and_maybe_stop(runtime)
        return

    if result is None:
        logger.warning("%s: content unchanged, skipping", issue_ref)
        await _release_and_maybe_stop(runtime)
        return

    runtime.progress.update(
        runtime.overall_id,
        description=f"[dim]{issue_ref} ({worker_name}):[/dim] embed…",
    )
    try:
        embedding, embed_elapsed = await _timed(
            _embed_summary(
                runtime.embed_client,
                title=issue_data["title"],
                summary=result["summary"],
            )
        )
    except LLMQuotaError:
        runtime.progress.update(runtime.overall_id, description="Evaluating issues")
        await runtime.state.release()
        await _enter_quota_backoff(runtime)
        return
    except Exception:
        runtime.progress.update(runtime.overall_id, description="Evaluating issues")
        logger.exception("%s: embedding failed", issue_ref)
        await _release_and_maybe_stop(runtime)
        return

    try:
        search_embedding = await _embed_search_text(
            runtime.embed_client,
            title=issue_data["title"],
            body=issue_data.get("body"),
        )
    except LLMQuotaError:
        runtime.progress.update(runtime.overall_id, description="Evaluating issues")
        await runtime.state.release()
        await _enter_quota_backoff(runtime)
        return
    except Exception:
        runtime.progress.update(runtime.overall_id, description="Evaluating issues")
        logger.exception("%s: search embedding failed", issue_ref)
        await _release_and_maybe_stop(runtime)
        return

    submission: dict[str, Any] = {
        "issue_id": issue_data["issue_id"],
        "content_hash": result["issue_data_hash"],
        "summary": result["summary"],
        "scores": result["scores"],
        "suggested_action": result["suggested_action"],
        "suggested_action_reason": result["suggested_action_reason"],
        "tokens_used": result["tokens_used"],
        "prompt_tokens": result["prompt_tokens"],
        "completion_tokens": result["completion_tokens"],
        "model_used": runtime.model,
        "llm_backend": runtime.llm_backend,
        "cost_usd": result["cost_usd"],
        "summary_embedding": embedding,
        "search_embedding": search_embedding,
        "related_work": result.get("related_work", []),
        "transcript": result.get("transcript"),
        "evidence_paths": _serialize_evidence_paths(result.get("tool_context")),
    }

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
        return

    if submit_response.status_code == HTTP_CONFLICT:
        logger.warning("%s: content changed during evaluation, skipped", issue_ref)
        await _release_and_maybe_stop(runtime)
        return

    if submit_response.status_code != HTTP_OK:
        logger.error(
            "%s: submit failed %d from %s: %s",
            issue_ref,
            submit_response.status_code,
            submit_response.url,
            _format_error_body(submit_response),
        )
        await _release_and_maybe_stop(runtime)
        return

    completed = await runtime.state.complete(
        prompt_tokens=submission["prompt_tokens"],
        completion_tokens=submission["completion_tokens"],
    )
    wall_elapsed = time.monotonic() - started_at
    runtime.timing.add(PHASE_EVALUATE, wall_elapsed)
    runtime.progress.update(
        runtime.overall_id, advance=1, description="Evaluating issues"
    )
    action = submission.get("suggested_action") or "summary_only"
    runtime.progress.console.print(
        f"[bold]{issue_ref}[/bold] — {action}"
        f"  [dim]{submission['prompt_tokens']} in / {submission['completion_tokens']} out"
        f"  eval {_format_elapsed(evaluate_elapsed)}"
        f"  embed {_format_elapsed(embed_elapsed)}[/dim]"
    )

    if runtime.issue_limit > 0 and completed >= runtime.issue_limit:
        logger.info("Done: evaluated %d issues", runtime.issue_limit)
        shutdown_state["requested"] = True


async def _run_issue_preflight(
    runtime: _Runtime,
    *,
    issue_data: dict[str, Any],
    worker_name: str,
) -> bool:
    """Return whether a claimed issue passed preflight."""
    issue_ref = f"{issue_data['project_name']}#{issue_data['external_id']}"
    runtime.progress.update(
        runtime.overall_id,
        description=f"[dim]{issue_ref} ({worker_name}):[/dim] preflight…",
    )

    async def _sync_claimed_repo(project: str) -> bool:
        try:
            clone_url = clone_url_for(
                project, allowed_projects=runtime.allowed_projects
            )
        except Exception:
            logger.warning("%s: no clone URL available for %s", issue_ref, project)
            return False

        result = await sync_mirror(
            project,
            clone_url=clone_url,
            mirror_dir=runtime.mirror_dir,
        )
        return result.status != "skipped"

    async def _release_claim(*, issue_id: int, reason: str) -> None:
        try:
            response = await runtime.http_client.post(
                "/api/eval/release",
                json={"issue_id": issue_id, "reason": reason},
                headers=runtime.headers,
            )
        except httpx.HTTPError:
            logger.warning("%s: failed to release claim", issue_ref, exc_info=True)
            return

        if response.status_code != HTTP_OK:
            logger.warning(
                "%s: release claim failed %d from %s: %s",
                issue_ref,
                response.status_code,
                response.url,
                _format_error_body(response),
            )

    async def _check_related_endpoint() -> bool:
        query = (issue_data.get("title") or issue_ref)[:1000]
        embedding: list[float] | None = None
        if runtime.embed_client is not None and query:
            try:
                embedding = await runtime.embed_client.embed(query, dimensions=1024)
            except Exception:
                return False

        if embedding is not None:
            response = await runtime.http_client.post(
                "/api/eval/related",
                json={
                    "issue_id": issue_data["issue_id"],
                    "query": query,
                    "embedding": embedding,
                },
                headers=runtime.headers,
            )
        else:
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
        release_claim=_release_claim,
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
        await _evaluate_issue(runtime, issue_data=issue_data, worker_name=worker_name)


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
    embed_model: str = "openai/text-embedding-3-small",
    issue: str = "",
    concurrency: int = 10,
) -> None:
    """Run the continuous HTTP evaluation worker against ``/api/eval/*``.

    Each worker coroutine independently polls ``GET /api/eval/next``, evaluates
    the claimed issue via the selected chat backend, computes an OpenRouter
    embedding for the resulting summary, and submits the finished payload to
    ``POST /api/eval/result``. No direct database access is used.
    """
    global _quota_paused  # noqa: PLW0603
    shutdown_state["requested"] = False
    paused_state["paused"] = False
    _quota_paused = False
    signal.signal(signal.SIGINT, _signal_handler)
    console = Console()
    _setup_logging(verbose=verbose, console=console)

    server_url = server.rstrip("/")
    verify: bool | str = server_ca_cert if server_ca_cert else True
    headers = {"Authorization": "Bearer " + token}
    params = {
        "project": project,
        "open_only": open_only,
        "force": force,
        "incomplete": incomplete,
        "stale_days": stale_days,
        "external_id": issue,
    }
    if issue:
        limit = 1
    settings = Settings()
    config = load_config(settings.config_path)

    llm_client = create_llm_client_for_backend(
        llm_backend=llm_backend,
        openrouter_api_key=openrouter_api_key,
        llm_url=llm_url,
        llm_api_key=llm_api_key,
        ca_cert=ca_cert,
    )
    evaluator = IssueEvaluator(
        client=llm_client,
        model_summary=model_summary,
        model_scoring=model_scoring,
    )
    embed_client = EmbeddingClient(
        base_url=OPENROUTER_BASE_URL,
        model=embed_model,
        api_key=openrouter_api_key,
        ca_cert="",
    )

    filter_parts = []
    if project:
        filter_parts.append(project)
    if open_only:
        filter_parts.append("open only")
    filter_desc = f" ({', '.join(filter_parts)})" if filter_parts else ""

    try:
        async with httpx.AsyncClient(
            base_url=server_url, timeout=30.0, verify=verify
        ) as http_client:
            project_orgs: dict[str, str] = {}
            try:
                projects_resp = await http_client.get(
                    "/api/eval/projects", headers=headers
                )
                if projects_resp.status_code == HTTP_OK:
                    project_orgs = projects_resp.json().get("projects", {})
            except httpx.HTTPError:
                pass
            allowed_projects = resolve_allowed_projects(
                craft_projects=config.craft_projects,
                project_orgs=project_orgs,
            )

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
                        "Connected to %s%s — %d/%d open issues evaluated (%d%%), %d pending | backend=%s model=%s concurrency=%d",
                        server_url,
                        filter_desc,
                        total_evaluated,
                        total_open,
                        already_pct,
                        server_pending,
                        llm_backend,
                        model_scoring,
                        concurrency,
                    )
            except httpx.HTTPError:
                logger.info(
                    "Connected to %s%s | backend=%s model=%s concurrency=%d",
                    server_url,
                    filter_desc,
                    llm_backend,
                    model_scoring,
                    concurrency,
                )

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
                    embed_client=embed_client,
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
                )
                await asyncio.gather(
                    *(
                        _worker_loop(runtime, server_url=server_url, worker_index=index)
                        for index in range(1, concurrency + 1)
                    )
                )

                if state.evaluated > 0:
                    logger.info(
                        "Run total: %d issues, %d in / %d out tokens (%d total)",
                        state.evaluated,
                        state.total_prompt_tokens,
                        state.total_completion_tokens,
                        state.total_prompt_tokens + state.total_completion_tokens,
                    )
    finally:
        await llm_client.close()
        await embed_client.close()
