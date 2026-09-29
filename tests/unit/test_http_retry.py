"""Tests for the shared outbound HTTP retry policy."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
from craft_dashboard.http_retry import (
    is_retriable_http_error,
    make_before_sleep_log,
    make_wait_honoring_retry_after,
    retry_after_seconds,
)


def _status_error(
    status: int, headers: dict[str, str] | None = None
) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(status, request=request, headers=headers or {})
    return httpx.HTTPStatusError(str(status), request=request, response=response)


class TestIsRetriableHttpError:
    def test_retries_on_429(self) -> None:
        assert is_retriable_http_error(_status_error(429)) is True

    def test_does_not_retry_on_404(self) -> None:
        assert is_retriable_http_error(_status_error(404)) is False

    def test_does_not_retry_on_402(self) -> None:
        """402 means the budget is gone; retrying bills without succeeding."""
        assert is_retriable_http_error(_status_error(402)) is False

    def test_retries_on_transport_error(self) -> None:
        assert is_retriable_http_error(httpx.ConnectTimeout("timeout")) is True


class TestRetryAfterSeconds:
    def test_parses_header(self) -> None:
        assert retry_after_seconds(_status_error(429, {"Retry-After": "12"})) == 12.0

    def test_missing_header(self) -> None:
        assert retry_after_seconds(_status_error(429)) is None

    def test_non_status_error(self) -> None:
        assert retry_after_seconds(httpx.ConnectTimeout("timeout")) is None

    def test_unparseable_header_falls_back(self) -> None:
        """The HTTP-date form is not parsed; backoff handles it instead."""
        assert (
            retry_after_seconds(
                _status_error(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
            )
            is None
        )


class TestMakeWaitHonoringRetryAfter:
    def test_retry_after_takes_precedence_over_backoff(self) -> None:
        wait = make_wait_honoring_retry_after(lambda _state: 99.0)
        state = SimpleNamespace(
            outcome=SimpleNamespace(
                exception=lambda: _status_error(429, {"Retry-After": "7"})
            )
        )
        assert wait(state) == 7.0

    def test_falls_back_to_default_without_header(self) -> None:
        wait = make_wait_honoring_retry_after(lambda _state: 99.0)
        state = SimpleNamespace(
            outcome=SimpleNamespace(exception=lambda: httpx.ConnectTimeout("t"))
        )
        assert wait(state) == 99.0

    def test_falls_back_when_no_outcome(self) -> None:
        wait = make_wait_honoring_retry_after(lambda _state: 99.0)
        assert wait(SimpleNamespace(outcome=None)) == 99.0


class TestMakeBeforeSleepLog:
    def test_logs_exception_type_when_str_empty(self, caplog) -> None:
        """Logs the class name when str(exc) is empty, e.g. ReadTimeout."""
        logger = logging.getLogger("test_http_retry")
        hook = make_before_sleep_log(logger, 3)
        state = SimpleNamespace(
            attempt_number=1,
            outcome=SimpleNamespace(exception=lambda: httpx.ReadTimeout("")),
        )
        with caplog.at_level(logging.WARNING):
            hook(state)

        assert "HTTP retry (attempt 1/3): ReadTimeout" in caplog.text

    def test_logs_exception_type_and_message(self, caplog) -> None:
        logger = logging.getLogger("test_http_retry")
        hook = make_before_sleep_log(logger, 5)
        state = SimpleNamespace(
            attempt_number=2,
            outcome=SimpleNamespace(
                exception=lambda: httpx.ConnectError("Connection refused")
            ),
        )
        with caplog.at_level(logging.WARNING):
            hook(state)

        assert (
            "HTTP retry (attempt 2/5): ConnectError: Connection refused" in caplog.text
        )

    def test_custom_prefix_and_level(self, caplog) -> None:
        logger = logging.getLogger("test_http_retry")
        hook = make_before_sleep_log(
            logger, 6, level=logging.DEBUG, prefix="Forum HTTP retry"
        )
        state = SimpleNamespace(
            attempt_number=1,
            outcome=SimpleNamespace(exception=lambda: httpx.ConnectError("boom")),
        )
        with caplog.at_level(logging.DEBUG):
            hook(state)

        assert "Forum HTTP retry (attempt 1/6): ConnectError: boom" in caplog.text

    def test_no_log_without_exception(self, caplog) -> None:
        logger = logging.getLogger("test_http_retry")
        hook = make_before_sleep_log(logger, 3)
        with caplog.at_level(logging.DEBUG):
            hook(SimpleNamespace(attempt_number=1, outcome=None))

        assert caplog.text == ""
