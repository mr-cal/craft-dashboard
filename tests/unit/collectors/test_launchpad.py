"""Tests for the Launchpad data collector."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from craft_dashboard.collectors.launchpad import (
    LaunchpadCollector,
    _fetch_bug_comments,
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
            "Opinion",
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


class TestCollectBugsIncremental:
    """Tests for incremental Launchpad bug fetching using modified_since."""

    def _make_mock_task(self) -> MagicMock:
        mock_bug = MagicMock()
        mock_bug.id = 999
        mock_bug.title = "Incremental bug"
        mock_bug.description = ""
        mock_bug.tags = []
        mock_bug.date_created = datetime(2024, 1, 1, tzinfo=UTC)
        mock_bug.date_last_updated = datetime(2024, 6, 1, tzinfo=UTC)
        mock_bug.web_link = "https://bugs.launchpad.net/bugs/999"
        mock_task = MagicMock()
        mock_task.bug = mock_bug
        mock_task.status = "New"
        mock_task.owner_link = None
        mock_task.importance = "Low"
        mock_task.date_closed = None
        return mock_task

    async def test_first_run_fetches_all_no_modified_since(self, mocker) -> None:
        """On first run (no prior data), searchTasks is called WITHOUT modified_since."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        mock_project = MagicMock()
        mock_project.searchTasks.return_value = [self._make_mock_task()]
        mock_lp = MagicMock()
        mock_lp.projects.__getitem__.return_value = mock_project
        mocker.patch.object(collector, "_get_launchpad", return_value=mock_lp)

        mock_session = AsyncMock()
        mock_session.scalar.return_value = None  # No prior data

        mocker.patch("sqlalchemy.dialects.postgresql.insert", return_value=MagicMock())

        await collector.collect_bugs("snapcraft", 1, mock_session)

        call_kwargs = mock_project.searchTasks.call_args.kwargs
        assert "modified_since" not in call_kwargs, (
            "First run should not pass modified_since"
        )

    async def test_subsequent_run_passes_modified_since(self, mocker) -> None:
        """On subsequent runs, searchTasks is called WITH modified_since=last_fetched."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        mock_project = MagicMock()
        mock_project.searchTasks.return_value = []
        mock_lp = MagicMock()
        mock_lp.projects.__getitem__.return_value = mock_project
        mocker.patch.object(collector, "_get_launchpad", return_value=mock_lp)

        last_fetched = datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC)
        mock_session = AsyncMock()
        mock_session.scalar.return_value = last_fetched

        await collector.collect_bugs("snapcraft", 1, mock_session)

        call_kwargs = mock_project.searchTasks.call_args.kwargs
        assert "modified_since" in call_kwargs, (
            "Subsequent run should pass modified_since"
        )
        assert call_kwargs["modified_since"] == last_fetched


def _make_mock_message(owner_link: str | None, content: str, created: datetime):
    """Create a mock Launchpad bug message (comment)."""
    message = MagicMock()
    message.owner_link = owner_link
    message.content = content
    message.date_created = created
    return message


class TestFetchBugComments:
    """Tests for _fetch_bug_comments()."""

    def test_skips_first_message_as_description(self) -> None:
        """The first bug.messages entry (the description) is excluded."""
        mock_bug = MagicMock()
        mock_bug.messages = [
            _make_mock_message(
                "https://api.launchpad.net/1.0/~author",
                "Original description",
                datetime(2024, 1, 1, tzinfo=UTC),
            ),
            _make_mock_message(
                "https://api.launchpad.net/1.0/~commenter",
                "A reply",
                datetime(2024, 1, 2, tzinfo=UTC),
            ),
        ]

        comments = _fetch_bug_comments(mock_bug)

        assert len(comments) == 1
        assert comments[0]["author"] == "~commenter"
        assert comments[0]["body"] == "A reply"
        assert comments[0]["type"] == "comment"

    def test_no_messages_beyond_description_returns_empty(self) -> None:
        """A bug with only the description message has no comments."""
        mock_bug = MagicMock()
        mock_bug.messages = [
            _make_mock_message(
                "https://api.launchpad.net/1.0/~author",
                "Original description",
                datetime(2024, 1, 1, tzinfo=UTC),
            ),
        ]

        assert _fetch_bug_comments(mock_bug) == []

    def test_keeps_only_last_50_comments(self) -> None:
        """Only the most recent 50 comments (after the description) are kept."""
        mock_bug = MagicMock()
        description = _make_mock_message(
            "https://api.launchpad.net/1.0/~author",
            "Original description",
            datetime(2024, 1, 1, tzinfo=UTC),
        )
        replies = [
            _make_mock_message(
                "https://api.launchpad.net/1.0/~commenter",
                f"Reply {i}",
                datetime(2024, 1, 2, tzinfo=UTC),
            )
            for i in range(60)
        ]
        mock_bug.messages = [description, *replies]

        comments = _fetch_bug_comments(mock_bug)

        assert len(comments) == 50
        # Should keep the most recent 50, i.e. replies 10..59.
        assert comments[0]["body"] == "Reply 10"
        assert comments[-1]["body"] == "Reply 59"

    def test_truncates_long_comment_body(self) -> None:
        """A comment body longer than 1000 chars is truncated."""
        mock_bug = MagicMock()
        mock_bug.messages = [
            _make_mock_message(
                "https://api.launchpad.net/1.0/~author",
                "Original description",
                datetime(2024, 1, 1, tzinfo=UTC),
            ),
            _make_mock_message(
                "https://api.launchpad.net/1.0/~commenter",
                "x" * 2000,
                datetime(2024, 1, 2, tzinfo=UTC),
            ),
        ]

        comments = _fetch_bug_comments(mock_bug)

        assert len(comments[0]["body"]) == 1000

    def test_none_owner_link_defaults_to_unknown(self) -> None:
        """A message with no owner_link is attributed to 'unknown'."""
        mock_bug = MagicMock()
        mock_bug.messages = [
            _make_mock_message(
                "https://api.launchpad.net/1.0/~author",
                "Original description",
                datetime(2024, 1, 1, tzinfo=UTC),
            ),
            _make_mock_message(None, "A reply", datetime(2024, 1, 2, tzinfo=UTC)),
        ]

        comments = _fetch_bug_comments(mock_bug)

        assert comments[0]["author"] == "unknown"

    def test_none_date_created_stores_none(self) -> None:
        """A message with no date_created stores created_at=None."""
        mock_bug = MagicMock()
        mock_bug.messages = [
            _make_mock_message(
                "https://api.launchpad.net/1.0/~author",
                "Original description",
                datetime(2024, 1, 1, tzinfo=UTC),
            ),
            _make_mock_message(
                "https://api.launchpad.net/1.0/~commenter", "A reply", None
            ),
        ]

        comments = _fetch_bug_comments(mock_bug)

        assert comments[0]["created_at"] is None


class TestCollectBugsComments:
    """Tests for comment fetching/storage/hashing inside collect_bugs()."""

    def _make_mock_task(self, messages: list) -> MagicMock:
        mock_bug = MagicMock()
        mock_bug.id = 789
        mock_bug.title = "Bug with comments"
        mock_bug.description = "Original description"
        mock_bug.tags = []
        mock_bug.date_created = datetime(2024, 1, 1, tzinfo=UTC)
        mock_bug.date_last_updated = datetime(2024, 1, 2, tzinfo=UTC)
        mock_bug.web_link = "https://bugs.launchpad.net/bugs/789"
        mock_bug.messages = messages

        mock_task = MagicMock()
        mock_task.bug = mock_bug
        mock_task.status = "New"
        mock_task.owner_link = "https://api.launchpad.net/1.0/~author"
        mock_task.importance = "Low"
        mock_task.date_closed = None
        return mock_task

    def _make_mock_lp(self, tasks: list) -> MagicMock:
        mock_project = MagicMock()
        mock_project.searchTasks.return_value = tasks
        mock_lp = MagicMock()
        mock_lp.projects.__getitem__.return_value = mock_project
        return mock_lp

    def _make_insert_patch(self, captured: dict):
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

    async def test_comments_stored_on_issue(self, mocker) -> None:
        """Fetched comments (excluding the description) are stored on the Issue."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        messages = [
            _make_mock_message(
                "https://api.launchpad.net/1.0/~author",
                "Original description",
                datetime(2024, 1, 1, tzinfo=UTC),
            ),
            _make_mock_message(
                "https://api.launchpad.net/1.0/~commenter",
                "A reply",
                datetime(2024, 1, 2, tzinfo=UTC),
            ),
        ]
        mock_task = self._make_mock_task(messages)
        mocker.patch.object(
            collector, "_get_launchpad", return_value=self._make_mock_lp([mock_task])
        )

        captured: dict = {}
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._make_insert_patch(captured),
        )

        await collector.collect_bugs("snapcraft", 1, AsyncMock())

        assert captured["comments"] == [
            {
                "author": "~commenter",
                "body": "A reply",
                "created_at": "2024-01-02T00:00:00+00:00",
                "type": "comment",
            }
        ]

    async def test_no_comments_beyond_description_stores_empty_list(
        self, mocker
    ) -> None:
        """A bug with no replies (only the description) stores comments=[]."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        messages = [
            _make_mock_message(
                "https://api.launchpad.net/1.0/~author",
                "Original description",
                datetime(2024, 1, 1, tzinfo=UTC),
            ),
        ]
        mock_task = self._make_mock_task(messages)
        mocker.patch.object(
            collector, "_get_launchpad", return_value=self._make_mock_lp([mock_task])
        )

        captured: dict = {}
        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._make_insert_patch(captured),
        )

        await collector.collect_bugs("snapcraft", 1, AsyncMock())

        assert captured["comments"] == []

    async def test_new_comment_changes_content_hash(self, mocker) -> None:
        """Adding a new comment to an otherwise-unchanged bug changes content_hash."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        description_message = _make_mock_message(
            "https://api.launchpad.net/1.0/~author",
            "Original description",
            datetime(2024, 1, 1, tzinfo=UTC),
        )

        async def _run_with_messages(messages: list) -> str:
            mock_task = self._make_mock_task(messages)
            mocker.patch.object(
                collector,
                "_get_launchpad",
                return_value=self._make_mock_lp([mock_task]),
            )
            captured: dict = {}
            mocker.patch(
                "sqlalchemy.dialects.postgresql.insert",
                side_effect=self._make_insert_patch(captured),
            )
            await collector.collect_bugs("snapcraft", 1, AsyncMock())
            return captured["content_hash"]

        hash_without_comment = await _run_with_messages([description_message])
        hash_with_comment = await _run_with_messages(
            [
                description_message,
                _make_mock_message(
                    "https://api.launchpad.net/1.0/~commenter",
                    "A new reply",
                    datetime(2024, 1, 3, tzinfo=UTC),
                ),
            ]
        )

        assert hash_without_comment != hash_with_comment

    async def test_comment_fetch_failure_does_not_abort_collection(
        self, mocker
    ) -> None:
        """A bug whose comments fail to fetch still gets collected, with comments=[]."""
        collector = LaunchpadCollector(projects=["snapcraft"])
        mock_task = self._make_mock_task([])
        # Force _fetch_bug_comments (called with this bug) to raise.
        mocker.patch(
            "craft_dashboard.collectors.launchpad._fetch_bug_comments",
            side_effect=RuntimeError("boom"),
        )
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
        assert captured["comments"] == []
