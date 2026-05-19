"""Tests for the Launchpad data collector."""


from craft_dashboard.collectors.launchpad import (
    LaunchpadCollector,
    _map_lp_status,
)


class TestMapLpStatus:
    """Tests for _map_lp_status."""

    def test_open_statuses(self) -> None:
        """Various Launchpad open statuses map to 'open'."""
        for status in ["New", "Confirmed", "Triaged", "In Progress", "Incomplete"]:
            assert _map_lp_status(status) == "open"

    def test_closed_statuses(self) -> None:
        """Various Launchpad closed statuses map to 'closed'."""
        for status in [
            "Fix Released",
            "Fix Committed",
            "Invalid",
            "Won't Fix",
            "Expired",
        ]:
            assert _map_lp_status(status) == "closed"

    def test_unknown_status_defaults_to_open(self) -> None:
        """Unknown statuses default to 'open'."""
        assert _map_lp_status("SomeUnknownStatus") == "open"


class TestLaunchpadCollector:
    """Tests for LaunchpadCollector."""

    def test_init(self) -> None:
        """LaunchpadCollector initializes with project list."""
        collector = LaunchpadCollector(projects=["snapcraft"])

        assert collector.projects == ["snapcraft"]
