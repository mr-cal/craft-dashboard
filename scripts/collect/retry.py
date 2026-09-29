"""Retry wrapper for transient GitHub API failures."""

import asyncio
import json
import logging

import requests
import urllib3
from github import GithubException

from scripts.collect.reporting import _summarize_exception

logger = logging.getLogger(__name__)


_GITHUB_RETRY_ATTEMPTS = 3
_GITHUB_RETRY_BASE_SLEEP = 30  # seconds; doubles each attempt

# Transient errors that PyGithub itself doesn't wrap in GithubException:
# GitHub occasionally truncates a large GraphQL response mid-stream (seen
# repeatedly on canonical/craft-parts, whose PRs carry unusually large
# nested checkSuites/checkRuns payloads). This surfaces as one of:
#   - urllib3.exceptions.ProtocolError / requests ChunkedEncodingError
#     ("Response ended prematurely") when the HTTP connection drops before
#     all chunks arrive,
#   - json.JSONDecodeError ("Unterminated string...") when PyGithub tries
#     to parse the truncated body as JSON,
#   - TypeError ("argument of type 'NoneType' is not iterable") from
#     PyGithub's own `if "errors" in data` check, when the truncated body
#     failed the safety check inside __structuredFromJson to become an
#     empty string, so json.loads never runs and `data` stays None.
# None of these are GithubException, so they used to bypass retry entirely
# and permanently fail the run.
_TRANSIENT_NETWORK_ERRORS = (
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
    urllib3.exceptions.ProtocolError,
    json.JSONDecodeError,
    TypeError,
)


async def _retry_github(coro_fn: object, description: str) -> object:
    """Run an async GitHub API call, retrying on transient errors.

    Retries up to _GITHUB_RETRY_ATTEMPTS times with exponential backoff on
    GithubException (covers 401 transient hiccups, 5xx, and connection
    errors) as well as on transient network/parse errors that PyGithub
    doesn't wrap in GithubException (dropped/truncated HTTP responses —
    see _TRANSIENT_NETWORK_ERRORS). Re-raises on the final attempt.
    """
    import collections.abc

    for attempt in range(_GITHUB_RETRY_ATTEMPTS):
        try:
            result = coro_fn()
            if isinstance(result, collections.abc.Coroutine):
                return await result
        except GithubException as exc:
            if attempt == _GITHUB_RETRY_ATTEMPTS - 1:
                raise
            sleep = _GITHUB_RETRY_BASE_SLEEP * (2**attempt)
            logger.warning(
                "GitHub API error during %s (attempt %d/%d): %s — retrying in %ds",
                description,
                attempt + 1,
                _GITHUB_RETRY_ATTEMPTS,
                _summarize_exception(exc),
                sleep,
            )
            await asyncio.sleep(sleep)
        except _TRANSIENT_NETWORK_ERRORS as exc:
            if attempt == _GITHUB_RETRY_ATTEMPTS - 1:
                raise
            sleep = _GITHUB_RETRY_BASE_SLEEP * (2**attempt)
            logger.warning(
                "Transient network/parse error during %s (attempt %d/%d): "
                "%s — retrying in %ds",
                description,
                attempt + 1,
                _GITHUB_RETRY_ATTEMPTS,
                _summarize_exception(exc),
                sleep,
            )
            await asyncio.sleep(sleep)
        else:
            return result
    msg = "unreachable"
    raise RuntimeError(msg)
