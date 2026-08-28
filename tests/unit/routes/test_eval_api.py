"""Unit tests for craft_dashboard.routes.eval_api helpers."""

from datetime import UTC, datetime, timedelta

import pytest
from craft_dashboard.routes import eval_api

from tests.factories import make_evaluation, make_issue, make_project


class TestEvalNextRateLimit:
    """Tests for the loopback rate-limit override on GET /api/eval/next."""

    def test_loopback_ipv4_gets_higher_limit(self) -> None:
        assert eval_api._eval_next_rate_limit("127.0.0.1") == "1000/minute"

    def test_loopback_ipv6_gets_higher_limit(self) -> None:
        assert eval_api._eval_next_rate_limit("::1") == "1000/minute"

    def test_non_loopback_keeps_default_limit(self) -> None:
        # 8.8.8.8 is a real, globally-routable public IP — ipaddress treats
        # some documentation/reserved ranges (e.g. 203.0.113.0/24) as
        # "private" too, so a genuinely public address is used here instead.
        assert eval_api._eval_next_rate_limit("8.8.8.8") == "30/minute"

    def test_private_container_network_gets_higher_limit(self) -> None:
        # The continuous evaluate worker calls craft-dashboard over the
        # shared Podman network (docker-compose.llm-evaluate.yml), so it
        # presents a private container IP, not a literal loopback address.
        assert eval_api._eval_next_rate_limit("10.89.0.42") == "1000/minute"

    def test_invalid_key_keeps_default_limit(self) -> None:
        assert eval_api._eval_next_rate_limit("not-an-ip") == "30/minute"


class TestGetEvalActivity:
    """Tests for the module-level /next and /result activity accessor."""

    def test_returns_none_before_any_activity(self, monkeypatch) -> None:
        monkeypatch.setattr(eval_api, "_last_next_call_at", None)
        monkeypatch.setattr(eval_api, "_last_result_submitted_at", None)

        assert eval_api.get_eval_activity() == (None, None)

    def test_returns_recorded_timestamps(self, monkeypatch) -> None:
        poll_at = datetime(2025, 1, 1, tzinfo=UTC)
        result_at = datetime(2025, 1, 2, tzinfo=UTC)
        monkeypatch.setattr(eval_api, "_last_next_call_at", poll_at)
        monkeypatch.setattr(eval_api, "_last_result_submitted_at", result_at)

        assert eval_api.get_eval_activity() == (poll_at, result_at)


class TestGetQuotaPauseUntil:
    """Tests for the module-level quota-pause report accessor."""

    def test_returns_none_when_never_reported(self, monkeypatch) -> None:
        monkeypatch.setattr(eval_api, "_quota_paused_until", None)

        assert eval_api.get_quota_pause_until() is None

    def test_returns_future_resume_time(self, monkeypatch) -> None:
        resume_at = datetime.now(UTC) + timedelta(minutes=20)
        monkeypatch.setattr(eval_api, "_quota_paused_until", resume_at)

        assert eval_api.get_quota_pause_until() == resume_at

    def test_expires_once_resume_time_has_passed(self, monkeypatch) -> None:
        # A stale report from a since-recovered worker must not linger and
        # misreport the service as still paused.
        resume_at = datetime.now(UTC) - timedelta(minutes=1)
        monkeypatch.setattr(eval_api, "_quota_paused_until", resume_at)

        assert eval_api.get_quota_pause_until() is None


async def _seed_evaluations_with_cost(test_db_session, *, costs: list[float]) -> None:
    project = make_project(id=1, name="snapcraft")
    test_db_session.add(project)
    await test_db_session.flush()

    now = datetime.now(tz=UTC)
    for idx, cost in enumerate(costs, start=1):
        issue = make_issue(project_id=project.id, external_id=str(idx))
        test_db_session.add(issue)
        await test_db_session.flush()
        test_db_session.add(
            make_evaluation(
                issue_id=issue.id,
                cost_usd=cost,
                evaluated_at=now,
            )
        )
    await test_db_session.commit()


class TestDailySpendCap:
    @pytest.mark.asyncio
    async def test_daily_spend_cap_trips_quota_pause(
        self, test_db_session, monkeypatch
    ) -> None:
        monkeypatch.setattr(eval_api.settings, "eval_daily_spend_cap_usd", 1.0)
        monkeypatch.setattr(eval_api, "_quota_paused_until", None)
        await _seed_evaluations_with_cost(test_db_session, costs=[0.60, 0.55])

        await eval_api._maybe_trip_daily_spend_cap(test_db_session)

        assert eval_api.get_quota_pause_until() is not None

    @pytest.mark.asyncio
    async def test_daily_spend_cap_disabled_when_zero(
        self, test_db_session, monkeypatch
    ) -> None:
        monkeypatch.setattr(eval_api.settings, "eval_daily_spend_cap_usd", 0.0)
        monkeypatch.setattr(eval_api, "_quota_paused_until", None)
        await _seed_evaluations_with_cost(test_db_session, costs=[5.00])

        await eval_api._maybe_trip_daily_spend_cap(test_db_session)

        assert eval_api.get_quota_pause_until() is None
