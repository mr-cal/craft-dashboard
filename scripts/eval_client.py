#!/usr/bin/env python3
"""Pull-based LLM evaluation client for craft-dashboard."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import signal
import sys
from datetime import UTC, datetime
from typing import Any

import click
import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.llm.client import LocalLLMClient
from craft_dashboard.llm.evaluator import IssueEvaluator, _compute_content_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

HTTP_OK = httpx.codes.OK
HTTP_NO_CONTENT = httpx.codes.NO_CONTENT
HTTP_CONFLICT = httpx.codes.CONFLICT
shutdown_state = {"requested": False}


def _signal_handler(signum, frame) -> None:
    del signum, frame
    shutdown_state["requested"] = True
    logger.info("Shutdown requested, finishing current evaluation...")


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
    model: str,
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
) -> None:
    """Poll the eval API, run local evaluation, and submit results."""
    signal.signal(signal.SIGINT, _signal_handler)

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
        summary_model=model,
        evaluation_model=model,
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
                except httpx.HTTPError:
                    logger.exception("Failed to fetch work from %s", server_url)
                    await _sleep_until_next_poll(poll_interval)
                    continue

                if response.status_code == HTTP_NO_CONTENT:
                    logger.info("No work available, sleeping %ds", poll_interval)
                    await _sleep_until_next_poll(poll_interval)
                    continue

                if response.status_code != HTTP_OK:
                    logger.error(
                        "Server returned %d: %s",
                        response.status_code,
                        response.text,
                    )
                    await _sleep_until_next_poll(poll_interval)
                    continue

                try:
                    issue_data = response.json()
                except ValueError:
                    logger.exception("Server returned invalid JSON: %s", response.text)
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

                author = issue_data.get("author") or ""
                maintainers = set(issue_data.get("maintainers", []))

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
                    logger.exception(
                        "Issue %s: evaluation failed",
                        issue_data["external_id"],
                    )
                    continue

                if result is None:
                    logger.info(
                        "Issue %s: content unchanged, skipping",
                        issue_data["external_id"],
                    )
                    continue

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
                    "model_used": model,
                    "llm_backend": "local",
                }

                try:
                    submit_response = await http_client.post(
                        "/api/eval/result",
                        json=submission,
                        headers=headers,
                    )
                except httpx.HTTPError:
                    logger.exception(
                        "Issue %s: failed to submit result",
                        issue_data["external_id"],
                    )
                    continue

                if submit_response.status_code == HTTP_CONFLICT:
                    logger.warning(
                        "Issue %s: content changed during evaluation, skipping",
                        issue_data["external_id"],
                    )
                    continue

                if submit_response.status_code != HTTP_OK:
                    logger.error(
                        "Issue %s: failed to submit result: %d %s",
                        issue_data["external_id"],
                        submit_response.status_code,
                        submit_response.text,
                    )
                    continue

                evaluated += 1
                logger.info(
                    "Evaluated %d: %s#%s — %s (%d tokens)",
                    evaluated,
                    issue_data["project_name"],
                    issue_data["external_id"],
                    result["suggested_action"] or "summary_only",
                    result["tokens_used"],
                )

                if limit > 0 and evaluated >= limit:
                    logger.info("Reached limit of %d evaluations", limit)
                    break
    finally:
        await llm_client.close()


@click.command()
@click.option("--server", required=True, help="Base URL of craft-dashboard server")
@click.option("--token", required=True, help="Eval API bearer token")
@click.option("--model", default="llama3.2", show_default=True, help="LLM model name")
@click.option(
    "--llm-url",
    default="http://localhost:11434/v1",
    show_default=True,
    help="OpenAI-compatible LLM endpoint",
)
@click.option(
    "--llm-api-key",
    default="",
    show_default=True,
    help="API key for the LLM endpoint",
)
@click.option(
    "--ca-cert",
    default="",
    show_default=False,
    type=click.Path(exists=True, dir_okay=False),
    help="PEM CA cert path for LLM server TLS verification",
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
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Force re-evaluation of matched issues",
)
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
    "--server-ca-cert",
    default="",
    show_default=False,
    type=click.Path(exists=True, dir_okay=False),
    help="PEM CA cert for verifying the craft-dashboard server TLS cert",
)
def main(  # noqa: PLR0913
    server: str,
    token: str,
    model: str,
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
) -> None:
    """Run the local evaluation client CLI."""
    asyncio.run(
        run_eval_loop(
            server=server,
            token=token,
            model=model,
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
        )
    )


if __name__ == "__main__":
    main()
