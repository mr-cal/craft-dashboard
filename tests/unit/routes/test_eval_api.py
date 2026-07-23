"""Unit tests for craft_dashboard.routes.eval_api helpers."""

from datetime import UTC, datetime

from craft_dashboard.routes import eval_api


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
