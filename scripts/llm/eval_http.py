"""HTTP calls the evaluation worker makes against the ``/api/eval/*`` queue.

Every function here is a thin, side-effect-free-apart-from-I/O wrapper around
one endpoint. Claim/backoff decisions stay in ``eval_worker``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from datetime import datetime

    from scripts.llm.worker_runtime import Runtime

logger = logging.getLogger(__name__)

HTTP_OK = httpx.codes.OK
HTTP_NO_CONTENT = httpx.codes.NO_CONTENT
HTTP_CONFLICT = httpx.codes.CONFLICT
HTTP_TOO_MANY = httpx.codes.TOO_MANY_REQUESTS

_MAX_ERROR_BODY = 200


def format_error_body(response: httpx.Response) -> str:
    """Return a compact, readable summary of a non-2xx response body."""
    content_type = response.headers.get("content-type", "")
    text = response.text.strip()
    if "json" in content_type or text.startswith(("{", "[")):
        try:
            data = response.json()
            raw: object = data
            if isinstance(data, dict):
                if "error" in data:
                    err = data["error"]
                    raw = err.get("message", err) if isinstance(err, dict) else err
                elif "detail" in data:
                    raw = data["detail"]
            return " ".join(str(raw).strip().split())
        except ValueError:
            pass
    if "html" in content_type:
        if response.status_code >= 500:  # noqa: PLR2004
            return (
                f"(HTML response from {response.url} — "
                "server error; check the craft-dashboard logs)"
            )
        return f"(HTML response from {response.url} — is the server URL correct?)"
    collapsed = " ".join(text.split())
    return (
        (collapsed[:_MAX_ERROR_BODY] + "…")
        if len(collapsed) > _MAX_ERROR_BODY
        else collapsed
    )


async def post_submission(
    runtime: Runtime,
    *,
    issue_ref: str,
    submission: dict[str, Any],
) -> httpx.Response | None:
    """POST an evaluation result back to the craft-dashboard API with retry backoff."""
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = await runtime.http_client.post(
                "/api/eval/result",
                json=submission,
                headers=runtime.headers,
            )
            if response.status_code in {502, 503, 504} and attempt < max_retries:
                logger.warning(
                    "%s: submit returned %d on attempt %d/%d; retrying in %ds...",
                    issue_ref,
                    response.status_code,
                    attempt,
                    max_retries,
                    2**attempt,
                )
                await asyncio.sleep(2**attempt)
                continue
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            if attempt < max_retries:
                logger.warning(
                    "%s: network error (%s) submitting result on attempt %d/%d; retrying in %ds...",
                    issue_ref,
                    exc,
                    attempt,
                    max_retries,
                    2**attempt,
                )
                await asyncio.sleep(2**attempt)
                continue
            logger.error(  # noqa: TRY400
                "%s: failed to submit result after %d attempts: %s",
                issue_ref,
                max_retries,
                exc,
            )
        except httpx.HTTPError as exc:
            logger.error("%s: HTTP error submitting result: %s", issue_ref, exc)  # noqa: TRY400
            return None
        else:
            return response
    return None


async def release_claim(
    runtime: Runtime,
    *,
    issue_id: int,
    issue_ref: str,
    reason: str,
) -> None:
    """Release a claimed issue back to the server queue."""
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
            format_error_body(response),
        )


async def report_quota_pause(runtime: Runtime, *, resume_at: datetime) -> None:
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


async def fetch_project_orgs(
    http_client: httpx.AsyncClient, *, headers: dict[str, str]
) -> dict[str, str]:
    """Return the server's project-to-org mapping, or ``{}`` if unavailable."""
    try:
        projects_resp = await http_client.get("/api/eval/projects", headers=headers)
    except httpx.HTTPError:
        return {}
    if projects_resp.status_code == HTTP_OK:
        return projects_resp.json().get("projects", {})
    return {}


async def log_queue_status(
    http_client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
    server_url: str,
    filter_desc: str,
    llm_backend: str,
    model_scoring: str,
    concurrency: int,
    limit: int,
) -> int:
    """Log the queue banner and return how many issues this run expects to do."""
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
    return total_remaining
