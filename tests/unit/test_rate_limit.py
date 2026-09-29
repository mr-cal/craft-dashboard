"""Tests for the shared rate-limiting helpers."""

from __future__ import annotations

from craft_dashboard.rate_limit import is_local_caller, local_aware_limit


class TestIsLocalCaller:
    def test_loopback_is_local(self) -> None:
        assert is_local_caller("127.0.0.1") is True

    def test_container_network_address_is_local(self) -> None:
        """The eval worker calls in over the shared Podman network, not loopback."""
        assert is_local_caller("10.89.0.7") is True

    def test_public_address_is_not_local(self) -> None:
        assert is_local_caller("8.8.8.8") is False

    def test_ipv6_loopback_is_local(self) -> None:
        assert is_local_caller("::1") is True

    def test_non_address_is_not_local(self) -> None:
        assert is_local_caller("not-an-ip") is False


class TestLocalAwareLimit:
    def test_local_caller_gets_local_limit(self) -> None:
        limit = local_aware_limit("1000/minute", "30/minute")
        assert limit("10.89.0.7") == "1000/minute"

    def test_remote_caller_gets_remote_limit(self) -> None:
        limit = local_aware_limit("1000/minute", "30/minute")
        assert limit("8.8.8.8") == "30/minute"

    def test_unparseable_key_gets_remote_limit(self) -> None:
        """An unrecognized key must not be given the privileged limit."""
        limit = local_aware_limit("1000/minute", "30/minute")
        assert limit("") == "30/minute"
