"""Tests for the Launchpad data collector."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

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

    def test_all_open_statuses_comprehensive(self) -> None:
        """All documented open statuses map correctly."""
        for status in [
            "Opinion",
            "Incomplete (with response)",
            "Incomplete (without response)",
        ]:
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

    def test_empty_string_defaults_to_open(self) -> None:
        """Empty string defaults to open."""
        assert _map_lp_status("") == "open"


class TestLaunchpadCollector:
    """Tests for LaunchpadCollector."""

    def test_init(self) -> None:
        """LaunchpadCollector initializes with project list."""
        collector = LaunchpadCollector(projects=["snapcraft"])

        assert collector.projects == ["snapcraft"]

    def test_init_no_args(self) -> None:
        """LaunchpadCollector with no args has empty defaults."""
        collector = LaunchpadCollector()

        assert collector.projects == []
        assert collector._maintainers == set()

    def test_init_with_launchpad_maintainers(self) -> None:
        """LaunchpadCollector stores launchpad_maintainers as a set."""
        collector = LaunchpadCollector(
            projects=["snapcraft"],
            launchpad_maintainers=["mr-cal", "kyrofa"],
        )

        assert collector._maintainers == {"mr-cal", "kyrofa"}

    def test_init_defaults_empty_maintainers(self) -> None:
        """LaunchpadCollector defaults to empty maintainers set."""
        collector = LaunchpadCollector(projects=["snapcraft"])

        assert collector._maintainers == set()


class TestCollectBugs:
    """Tests for LaunchpadCollector.collect_bugs()."""

    def _make_mock_task(self, owner_link: str) -> MagicMock:
        """Create a mock Launchpad bug task with the given owner_link."""
        mock_bug = MagicMock()
        mock_bug.id = 123
        mock_bug.title = "Test bug"
        mock_bug.description = "Bug description"
        mock_bug.tags = []
        mock_bug.date_created = datetime(2024, 1, 1, tzinfo=UTC)
        mock_bug.date_last_updated = datetime(2024, 1, 2, tzinfo=UTC)
        mock_bug.web_link = "https://bugs.launchpad.net/bugs/123"

        mock_task = MagicMock()
        mock_task.bug = mock_bug
        mock_task.status = "New"
        mock_task.owner_link = owner_link
        mock_task.importance = "Low"
        mock_task.date_closed = None
        return mock_task

    def _make_mock_lp(self, tasks: list) -> MagicMock:
        """Create a mock Launchpad client returning the given tasks."""
        mock_project = MagicMock()
        mock_project.searchTasks.return_value = tasks

        mock_lp = MagicMock()
        mock_lp.projects.__getitem__.return_value = mock_project
        return mock_lp

    def _make_insert_patch(self, captured: dict):
        """Return a fake insert function that captures values() kwargs."""

        def fake_insert(table):
            chain = MagicMock()
            chain.on_conflict_do_update.return_value = MagicMock()

            def capture_values(**kw):
                captured.update(kw)
                return chain

            stmt = MagicMock()
            stmt.values = capture_values
            return stmt

        return fake_insert

    async def test_collect_bugs_author_is_maintainer(self, mocker) -> None:
        """Bugs whose author is in the maintainers list set author_is_maintainer=True."""
        collector = LaunchpadCollector(
            projects=["snapcraft"],
            launchpad_maintainers=["~mr-cal"],
        )
        mock_task = self._make_mock_task("https://api.launchpad.net/1.0/~mr-cal")
        mocker.patch.object(
            collector, "_get_launchpad", return_value=self._make_mock_lp([mock_task])
        )

        captured: dict = {}
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._make_insert_patch(captured),
        )

        count = await collector.collect_bugs("snapcraft", 1, AsyncMock())

        assert count == 1
        assert captured["author_is_maintainer"] is True

    async def test_collect_bugs_author_not_maintainer(self, mocker) -> None:
        """Bugs whose author is not in the maintainers list set author_is_maintainer=False."""
        collector = LaunchpadCollector(
            projects=["snapcraft"],
            launchpad_maintainers=["~mr-cal"],
        )
        mock_task = self._make_mock_task("https://api.launchpad.net/1.0/~other-user")
        mocker.patch.object(
            collector, "_get_launchpad", return_value=self._make_mock_lp([mock_task])
        )

        captured: dict = {}
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._make_insert_patch(captured),
        )

        count = await collector.collect_bugs("snapcraft", 1, AsyncMock())

        assert count == 1
        assert captured["author_is_maintainer"] is False


class TestCollectBugsAuthorExtraction:
    """Tests for author extraction from owner_link."""

    def _make_mock_task(self, owner_link):
        mock_bug = MagicMock()
        mock_bug.id = 456
        mock_bug.title = "Another bug"
        mock_bug.description = ""
        mock_bug.tags = ["ui"]
        mock_bug.date_created = datetime(2024, 6, 1, tzinfo=UTC)
        mock_bug.date_last_updated = datetime(2024, 6, 2, tzinfo=UTC)
        mock_bug.web_link = "https://bugs.launchpad.net/bugs/456"
        mock_task = MagicMock()
        mock_task.bug = mock_bug
        mock_task.status = "Confirmed"
        mock_task.owner_link = owner_link
        mock_task.importance = "Medium"
        mock_task.date_closed = None
        return mock_task

    def _make_mock_lp(self, tasks):
        mock_project = MagicMock()
        mock_project.searchTasks.return_value = tasks
        mock_lp = MagicMock()
        mock_lp.projects.__getitem__.return_value = mock_project
        return mock_lp

    def _make_insert_patch(self, captured):
        def fake_insert(table):
            chain = MagicMock()
            chain.on_conflict_do_update.return_value = MagicMock()

            def capture_values(**kw):
                captured.update(kw)
                return chain

            stmt = MagicMock()
            stmt.values = capture_values
            return stmt

        return fake_insert

    async def test_author_extracted_from_url(self, mocker) -> None:
        """Author username is extracted from the last segment of owner_link."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        mock_task = self._make_mock_task("https://api.launchpad.net/1.0/~john-doe")
        mocker.patch.object(
            collector, "_get_launchpad", return_value=self._make_mock_lp([mock_task])
        )
        captured = {}
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._make_insert_patch(captured),
        )
        await collector.collect_bugs("snapcraft", 1, AsyncMock())
        assert captured["author"] == "~john-doe"

    async def test_author_is_bot_always_false(self, mocker) -> None:
        """Launchpad issues always have author_is_bot=False."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        mock_task = self._make_mock_task("https://api.launchpad.net/1.0/~someone")
        mocker.patch.object(
            collector, "_get_launchpad", return_value=self._make_mock_lp([mock_task])
        )
        captured = {}
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._make_insert_patch(captured),
        )
        await collector.collect_bugs("snapcraft", 1, AsyncMock())
        assert captured["author_is_bot"] is False

    async def test_none_owner_link(self, mocker) -> None:
        """None owner_link results in None author."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        mock_task = self._make_mock_task(None)
        mocker.patch.object(
            collector, "_get_launchpad", return_value=self._make_mock_lp([mock_task])
        )
        captured = {}
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._make_insert_patch(captured),
        )
        await collector.collect_bugs("snapcraft", 1, AsyncMock())
        assert captured["author"] is None
        assert captured["author_is_maintainer"] is False
