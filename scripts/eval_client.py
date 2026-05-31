#!/usr/bin/env python3
"""Pull-based LLM evaluation client for craft-dashboard."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import signal
import sys
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import click
import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.llm.client import LocalLLMClient
from craft_dashboard.llm.duplicate_detector import DuplicateDetector
from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.llm.evaluator import IssueEvaluator, _compute_content_hash

logger = logging.getLogger(__name__)

HTTP_OK = httpx.codes.OK
HTTP_NO_CONTENT = httpx.codes.NO_CONTENT
HTTP_CONFLICT = httpx.codes.CONFLICT
shutdown_state = {"requested": False}

_MAX_ERROR_BODY = 200


def _setup_logging(*, verbose: bool) -> None:
    """Configure logging based on verbosity level."""
    if verbose:
        fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
        logging.basicConfig(level=logging.INFO, format=fmt)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        # Suppress noisy loggers at default verbosity
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
        return "(HTML response — is the server URL correct?)"
    text = response.text.strip()
    return (text[:_MAX_ERROR_BODY] + "…") if len(text) > _MAX_ERROR_BODY else text


def _format_limit(limit: int) -> str:
    """Format the limit as a display string."""
    return str(limit) if limit > 0 else "∞"


def _signal_handler(signum, frame) -> None:
    del signum, frame
    shutdown_state["requested"] = True
    logger.info("Shutting down after current evaluation...")


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
    embedding_model: str,
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
) -> None:
    """Poll the eval API, run local evaluation, and submit results."""
    signal.signal(signal.SIGINT, _signal_handler)
    _setup_logging(verbose=verbose)

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
    embedding_client: EmbeddingClient | None = None
    if embedding_model:
        embedding_client = EmbeddingClient(
            base_url=llm_base_url,
            model=embedding_model,
            api_key=llm_api_key,
            ca_cert=ca_cert,
        )

    limit_str = _format_limit(limit)
    filter_parts = []
    if project:
        filter_parts.append(project)
    if open_only:
        filter_parts.append("open only")
    filter_desc = f" ({', '.join(filter_parts)})" if filter_parts else ""
    logger.info(
        "Connecting to %s%s, model=%s, limit=%s",
        server_url,
        filter_desc,
        evaluation_model,
        limit_str,
    )

    try:
        async with httpx.AsyncClient(
            base_url=server_url,
            timeout=30.0,
            verify=verify,
        ) as http_client:
            evaluated = 0
            while not shutdown_state["requested"]:
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
                        "Server returned %d: %s",
                        response.status_code,
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

                issue_ref = f"{issue_data['project_name']}#{issue_data['external_id']}"
                logger.info("Evaluating %s...", issue_ref)

                author = issue_data.get("author") or ""
                maintainers = set(issue_data.get("maintainers", []))

                t0 = time.monotonic()
                try:
                    result = await evaluator.evaluate_issue(
                        title=issue_data["title"],
                        body=issue_data.get("body"),
                        issue_type=issue_data["issue_type"],
                        state=issue_data["state"],
                        labels=issue_data.get("labels", []),
                        age_days=_days_since(issue_data.get("created_at")),
                        last_activity_days=_days_since(issue_data.get("updated_at")),
                        author=author,
                        is_maintainer=author in maintainers
                        or issue_data.get("author_association") == "MAINTAINER",
                        comment_count=len(issue_data.get("comments", [])),
                        comments=issue_data.get("comments"),
                        existing_hash=None,
                    )
                except Exception:
                    elapsed = _format_elapsed(time.monotonic() - t0)
                    logger.exception(
                        "%s: evaluation failed after %s",
                        issue_ref,
                        elapsed,
                    )
                    continue

                elapsed = _format_elapsed(time.monotonic() - t0)

                if result is None:
                    logger.info(
                        "%s: content unchanged, skipped (%s)",
                        issue_ref,
                        elapsed,
                    )
                    continue

                # Compute embedding for phase 2 duplicate detection (optional)
                embedding: list[float] | None = None
                if embedding_client and result.get("summary"):
                    try:
                        embedding = await embedding_client.embed(result["summary"])
                    except Exception:
                        logger.warning(
                            "%s: embedding failed, will skip duplicate detection",
                            issue_ref,
                        )

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
                    "model_used": evaluation_model,
                    "llm_backend": "local",
                    "embedding": embedding,
                }

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
                        "%s: submit failed %d: %s",
                        issue_ref,
                        submit_response.status_code,
                        _format_error_body(submit_response),
                    )
                    continue

                evaluated += 1
                action = result["suggested_action"] or "summary_only"
                logger.info(
                    "[%d/%s] %s — %s (%d tokens, %s)",
                    evaluated,
                    limit_str,
                    issue_ref,
                    action,
                    result["tokens_used"],
                    elapsed,
                )

                if limit > 0 and evaluated >= limit:
                    logger.info("Done: evaluated %d issues", limit)
                    break
    finally:
        await llm_client.close()
        if embedding_client:
            await embedding_client.close()


async def run_duplicate_detection_loop(  # noqa: PLR0913
    *,
    server: str,
    token: str,
    evaluation_model: str,
    summary_model: str,
    embedding_model: str,
    llm_url: str,
    llm_api_key: str,
    ca_cert: str,
    server_ca_cert: str,
    cosine_threshold: float,
    limit: int,
    verbose: bool,
) -> None:
    """Run phase-2 duplicate detection loop."""
    signal.signal(signal.SIGINT, _signal_handler)
    _setup_logging(verbose=verbose)

    server_url = server.rstrip("/")
    llm_base_url = llm_url.rstrip("/")
    verify: bool | str = server_ca_cert if server_ca_cert else True
    headers = {"Authorization": f"Bearer {token}"}
    limit_str = _format_limit(limit)

    llm_client = LocalLLMClient(
        base_url=llm_base_url,
        api_key=llm_api_key,
        ca_cert=ca_cert,
    )
    embedding_client = EmbeddingClient(
        base_url=llm_base_url,
        model=embedding_model,
        api_key=llm_api_key,
        ca_cert=ca_cert,
    )
    detector = DuplicateDetector(
        embedding_client=embedding_client,
        llm_client=llm_client,
        evaluation_model=evaluation_model,
        summary_model=summary_model,
    )

    try:
        async with httpx.AsyncClient(
            base_url=server_url,
            timeout=30.0,
            verify=verify,
        ) as http_client:
            # Check phase-1 readiness before proceeding
            try:
                status_response = await http_client.get(
                    "/api/eval/status", headers=headers
                )
                if status_response.status_code == HTTP_OK:
                    status = status_response.json()
                    pending = status.get("pending", 0)
                    locked = status.get("locked", 0)
                    if pending > 0 or locked > 0:
                        logger.warning(
                            "Phase 1 not complete: %d pending, %d locked. "
                            "Run 'evaluate' first for best results.",
                            pending,
                            locked,
                        )
            except httpx.HTTPError:
                logger.warning("Could not check phase-1 status, proceeding anyway")

            logger.info(
                "Starting duplicate detection (cosine threshold=%.2f, limit=%s)",
                cosine_threshold,
                limit_str,
            )

            processed = 0
            while not shutdown_state["requested"]:
                try:
                    work_response = await http_client.get(
                        "/api/eval/duplicate-work", headers=headers
                    )
                except httpx.ConnectError as exc:
                    hint = (
                        " (try http:// instead of https://)"
                        if "SSL" in str(exc) or "wrong version" in str(exc).lower()
                        else ""
                    )
                    logger.error("Cannot connect to %s%s", server_url, hint)  # noqa: TRY400
                    break

                if work_response.status_code == HTTP_NO_CONTENT:
                    logger.info("Done: no more issues to check")
                    break

                if work_response.status_code != HTTP_OK:
                    logger.error(
                        "Server returned %d: %s",
                        work_response.status_code,
                        _format_error_body(work_response),
                    )
                    break

                items = work_response.json().get("items", [])
                if not items:
                    logger.info("Done: no more issues to check")
                    break

                for item in items:
                    if shutdown_state["requested"]:
                        break

                    evaluation_id = item["evaluation_id"]
                    issue_ref = f"{item['project_name']}#{item['external_id']}"
                    logger.info("Checking duplicates for %s...", issue_ref)

                    embedding = item.get("embedding")
                    if not embedding:
                        logger.warning("%s: no embedding, skipping", issue_ref)
                        continue

                    t0 = time.monotonic()

                    # Find nearest neighbours via server
                    async def find_similar(
                        *,
                        embedding: list[float],
                        exclude_issue_id: int,
                        _item: dict[str, Any] = item,
                    ) -> list[dict[str, Any]]:
                        try:
                            resp = await http_client.post(
                                "/api/eval/similar",
                                json={
                                    "embedding": embedding,
                                    "exclude_issue_id": exclude_issue_id,
                                    "cosine_threshold": cosine_threshold,
                                    "limit": 5,
                                },
                                headers=headers,
                            )
                            if resp.status_code == HTTP_OK:
                                return resp.json().get("candidates", [])
                        except httpx.HTTPError:
                            pass
                        return []

                    try:
                        dup_result = await detector.check_duplicates(
                            issue_id=item["issue_id"],
                            project_name=item["project_name"],
                            title=item["title"],
                            summary=item["summary"] or "",
                            embedding=embedding,
                            find_similar_fn=find_similar,
                        )
                    except Exception:
                        logger.exception("%s: duplicate check failed", issue_ref)
                        continue

                    elapsed = _format_elapsed(time.monotonic() - t0)
                    candidates_compared = (dup_result or {}).get(
                        "candidates_compared", 0
                    )

                    duplicate_result: dict[str, Any] = {
                        "evaluation_id": evaluation_id,
                        "duplicateness": 0.0,
                        "candidates_compared": candidates_compared,
                    }

                    if dup_result and "duplicate_of_issue_id" in dup_result:
                        dup_ref = f"{dup_result['duplicate_of_project_name']}#{dup_result['duplicate_of_external_id']}"
                        logger.info(
                            "[%d/%s] %s — duplicate of %s (confidence %d%%, %d compared, %s)",
                            processed + 1,
                            limit_str,
                            issue_ref,
                            dup_ref,
                            dup_result["confidence"],
                            candidates_compared,
                            elapsed,
                        )

                        # Rewrite summary to note the duplicate
                        try:
                            new_summary = await detector.rewrite_summary(
                                original_summary=item["summary"] or "",
                                duplicate_refs=[dup_ref],
                            )
                            new_embedding = await embedding_client.embed(new_summary)
                        except Exception:
                            logger.warning(
                                "%s: summary rewrite failed, using original",
                                issue_ref,
                            )
                            new_summary = None
                            new_embedding = None

                        duplicate_result.update(
                            {
                                "duplicateness": float(dup_result["confidence"]),
                                "duplicate_of_issue_id": dup_result[
                                    "duplicate_of_issue_id"
                                ],
                                "updated_summary": new_summary,
                                "updated_embedding": new_embedding,
                            }
                        )
                    else:
                        logger.info(
                            "[%d/%s] %s — no duplicate (%d compared, %s)",
                            processed + 1,
                            limit_str,
                            issue_ref,
                            candidates_compared,
                            elapsed,
                        )

                    # Submit result
                    try:
                        submit_resp = await http_client.post(
                            "/api/eval/duplicate-result",
                            json=duplicate_result,
                            headers=headers,
                        )
                        if submit_resp.status_code == HTTP_CONFLICT:
                            logger.warning(
                                "%s: evaluation was superseded, skipping", issue_ref
                            )
                        elif submit_resp.status_code != HTTP_OK:
                            logger.error(
                                "%s: submit failed %d: %s",
                                issue_ref,
                                submit_resp.status_code,
                                _format_error_body(submit_resp),
                            )
                    except httpx.HTTPError as exc:
                        logger.error(  # noqa: TRY400
                            "%s: HTTP error submitting duplicate result: %s",
                            issue_ref,
                            exc,
                        )

                    processed += 1
                    if limit > 0 and processed >= limit:
                        logger.info("Done: checked %d issues", limit)
                        return
    finally:
        await llm_client.close()
        await embedding_client.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_COMMON_OPTIONS = [
    click.option(
        "--server",
        required=True,
        envvar="EVAL_CLIENT_SERVER",
        help="Base URL of craft-dashboard server [env: EVAL_CLIENT_SERVER]",
    ),
    click.option(
        "--token",
        required=True,
        envvar="EVAL_API_TOKEN",
        help="Eval API bearer token [env: EVAL_API_TOKEN]",
    ),
    click.option(
        "--llm-url",
        default="http://localhost:11434/v1",
        show_default=True,
        envvar="LOCAL_LLM_URL",
        help="OpenAI-compatible LLM endpoint [env: LOCAL_LLM_URL]",
    ),
    click.option(
        "--llm-api-key",
        default="",
        envvar="LOCAL_LLM_API_KEY",
        help="API key for the LLM endpoint [env: LOCAL_LLM_API_KEY]",
    ),
    click.option(
        "--ca-cert",
        default="",
        envvar="LOCAL_LLM_CA_CERT",
        help="PEM CA cert path for LLM server TLS verification [env: LOCAL_LLM_CA_CERT]",
    ),
    click.option(
        "--server-ca-cert",
        default="",
        envvar="EVAL_CLIENT_SERVER_CA_CERT",
        help="PEM CA cert for verifying the server TLS cert [env: EVAL_CLIENT_SERVER_CA_CERT]",
    ),
    click.option(
        "--verbose",
        is_flag=True,
        default=False,
        help="Show timestamps, URLs, and model details",
    ),
]


def _add_common_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Apply all shared CLI options to a command."""
    for option in reversed(_COMMON_OPTIONS):
        fn = option(fn)
    return fn


@click.group()
def cli() -> None:
    """craft-dashboard local evaluation client."""


@cli.command()
@_add_common_options
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
    "--embedding-model",
    default="",
    envvar="LOCAL_LLM_EMBEDDING_MODEL",
    help="Embedding model for phase-2 duplicate detection [env: LOCAL_LLM_EMBEDDING_MODEL]",
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
def evaluate(  # noqa: PLR0913
    server: str,
    token: str,
    summary_model: str,
    evaluation_model: str,
    embedding_model: str,
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
    """Run phase-1 evaluation: summarize and score issues."""
    asyncio.run(
        run_eval_loop(
            server=server,
            token=token,
            summary_model=summary_model,
            evaluation_model=evaluation_model,
            embedding_model=embedding_model,
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


@cli.command("detect-duplicates")
@_add_common_options
@click.option(
    "--evaluation-model",
    default="llama3.2",
    show_default=True,
    envvar="LOCAL_LLM_EVALUATION_MODEL",
    help="LLM model for duplicate confirmation [env: LOCAL_LLM_EVALUATION_MODEL]",
)
@click.option(
    "--summary-model",
    default="llama3.2",
    show_default=True,
    envvar="LOCAL_LLM_SUMMARY_MODEL",
    help="LLM model for summary rewriting [env: LOCAL_LLM_SUMMARY_MODEL]",
)
@click.option(
    "--embedding-model",
    default="nomic-embed-text",
    show_default=True,
    envvar="LOCAL_LLM_EMBEDDING_MODEL",
    help="Embedding model [env: LOCAL_LLM_EMBEDDING_MODEL]",
)
@click.option(
    "--cosine-threshold",
    default=0.15,
    show_default=True,
    type=click.FloatRange(min=0.0, max=2.0),
    help="Max cosine distance to consider as candidate (0=identical, 2=opposite)",
)
@click.option(
    "--limit",
    default=0,
    show_default=True,
    type=click.IntRange(min=0),
    help="Max issues to check before exit (0=unlimited)",
)
def detect_duplicates(  # noqa: PLR0913
    server: str,
    token: str,
    evaluation_model: str,
    summary_model: str,
    embedding_model: str,
    llm_url: str,
    llm_api_key: str,
    ca_cert: str,
    server_ca_cert: str,
    cosine_threshold: float,
    limit: int,
    verbose: bool,
) -> None:
    """Run phase-2 duplicate detection across all evaluated issues.

    Only run after phase-1 evaluation is complete. Requires an embedding
    model (LOCAL_LLM_EMBEDDING_MODEL) and a PostgreSQL server with pgvector.
    """
    asyncio.run(
        run_duplicate_detection_loop(
            server=server,
            token=token,
            evaluation_model=evaluation_model,
            summary_model=summary_model,
            embedding_model=embedding_model,
            llm_url=llm_url,
            llm_api_key=llm_api_key,
            ca_cert=ca_cert,
            server_ca_cert=server_ca_cert,
            cosine_threshold=cosine_threshold,
            limit=limit,
            verbose=verbose,
        )
    )


if __name__ == "__main__":
    # Load .env from the repo root so dev machine settings are picked up automatically.
    # This is intentionally dev-only — production runners set env vars directly.
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
    cli()
