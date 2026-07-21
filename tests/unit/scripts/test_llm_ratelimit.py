"""Tests for scripts.llm.ratelimit."""

from __future__ import annotations

import pytest
from scripts.llm.ratelimit import SharedRateLimiter, parse_retry_after


class TestParseRetryAfter:
    def test_parses_valid_seconds_string(self) -> None:
        assert parse_retry_after("5") == 5.0

    def test_parses_fractional_seconds_string(self) -> None:
        assert parse_retry_after("2.5") == 2.5

    def test_returns_none_for_missing_value(self) -> None:
        assert parse_retry_after(None) is None

    def test_returns_none_for_empty_string(self) -> None:
        assert parse_retry_after("") is None

    def test_returns_none_for_non_numeric_value(self) -> None:
        # HTTP-date form (e.g. "Wed, 21 Oct 2015 07:28:00 GMT") isn't
        # supported; callers fall back to exponential backoff.
        assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None

    def test_clamps_negative_values_to_zero(self) -> None:
        assert parse_retry_after("-5") == 0.0


class TestSharedRateLimiter:
    @pytest.mark.asyncio
    async def test_wait_if_throttled_is_noop_when_not_throttled(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        limiter = SharedRateLimiter(sleep=fake_sleep)
        await limiter.wait_if_throttled()

        assert sleeps == []

    @pytest.mark.asyncio
    async def test_report_rate_limited_respects_retry_after_header(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        limiter = SharedRateLimiter(sleep=fake_sleep)
        await limiter.report_rate_limited(retry_after=10.0)
        await limiter.wait_if_throttled()

        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(10.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_report_rate_limited_falls_back_to_exponential_backoff(
        self,
    ) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        limiter = SharedRateLimiter(min_backoff=1.0, max_backoff=60.0, sleep=fake_sleep)
        await limiter.report_rate_limited()
        await limiter.wait_if_throttled()

        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(1.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_consecutive_hits_double_the_backoff_each_time(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        limiter = SharedRateLimiter(min_backoff=1.0, max_backoff=60.0, sleep=fake_sleep)
        await limiter.report_rate_limited()
        await limiter.report_rate_limited()
        await limiter.wait_if_throttled()

        # Second hit's cooldown (now + 2s) extends past the first's (now + 1s),
        # so the effective wait reflects the larger, doubled backoff.
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(2.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_backoff_is_capped_at_max_backoff(self) -> None:
        sleeps: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        limiter = SharedRateLimiter(min_backoff=1.0, max_backoff=5.0, sleep=fake_sleep)
        for _ in range(10):
            await limiter.report_rate_limited()
        await limiter.wait_if_throttled()

        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(5.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_report_success_resets_consecutive_hit_counter(self) -> None:
        limiter = SharedRateLimiter(min_backoff=1.0, max_backoff=60.0)
        await limiter.report_rate_limited()
        await limiter.report_rate_limited()

        # Two consecutive hits without a reset would push the counter to 3,
        # doubling the backoff again on the next hit.
        assert limiter._consecutive_hits == 2

        await limiter.report_success()

        assert limiter._consecutive_hits == 0
