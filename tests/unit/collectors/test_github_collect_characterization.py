"""Characterization tests for GitHub issue collection behaviour."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy as sa
from craft_dashboard.collectors.github import GitHubCollector
from craft_dashboard.llm.content_hash import compute_content_hash
from craft_dashboard.models.collection_watermark import CollectionWatermark
from scripts.collect import github_pass
from scripts.collect import projects as collect_projects
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from tests.factories import make_project

_TEST_TOKEN = "ghp_test"


class _RecordingInsertStatement:
    def __init__(self) -> None:
        self.excluded = MagicMock()
        self.values_kwargs = None

    def values(self, **kwargs):
        self.values_kwargs = kwargs
        return self

    def on_conflict_do_update(self, **_kwargs):
        return self


class _SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc_info):
        return None


def _fake_insert(_table) -> _RecordingInsertStatement:
    return _RecordingInsertStatement()


def _make_rest_issue(number: int = 123) -> MagicMock:
    issue = MagicMock()
    bug_label = MagicMock()
    bug_label.name = "bug"
    snapcraft_label = MagicMock()
    snapcraft_label.name = "snapcraft"
    issue.number = number
    issue.title = "snapcraft pack fails"
    issue.body = "Build fails during prime."
    issue.state = "open"
    issue.user = MagicMock(login="craft-contributor")
    issue.labels = [bug_label, snapcraft_label]
    issue.created_at = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    issue.updated_at = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
    issue.closed_at = None
    issue.html_url = f"https://github.com/canonical/repo/issues/{number}"
    issue.pull_request = None
    return issue


def _count_result(value: int) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _no_existing_issue_result() -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = None
    return result


def test_build_issue_values_maps_rest_issue_fields_and_hashes_metadata() -> None:
    collector = GitHubCollector(
        token=_TEST_TOKEN,
        org="canonical",
        maintainers=["renovate[bot]"],
    )
    issue = _make_rest_issue()
    issue.user = MagicMock(login="renovate[bot]")
    issue.created_at = datetime(2025, 1, 1, 12, 0)
    issue.updated_at = datetime(2025, 1, 2, 12, 0)
    comments = [
        {
            "author": "reviewer",
            "body": "please update",
            "created_at": "2025-01-03T00:00:00+00:00",
            "type": "comment",
        }
    ]
    metadata = {
        "review_status": "changes_requested",
        "review_count": 1,
        "unresolved_review_comments": 2,
        "ci_passing": ["lint"],
        "ci_failing": ["tests"],
        "ci_pending": ["publish"],
        "diff_additions": 5,
        "diff_deletions": 1,
        "diff_files_changed": 2,
    }

    before = datetime.now(UTC)
    values = collector._build_issue_values(
        issue,
        project_id=7,
        issue_type="pull_request",
        state="open",
        comments=comments,
        extra_metadata=metadata,
    )
    after = datetime.now(UTC)

    assert values["project_id"] == 7
    assert values["source"] == "github"
    assert values["external_id"] == "123"
    assert values["issue_type"] == "pull_request"
    assert values["title"] == "snapcraft pack fails"
    assert values["body"] == "Build fails during prime."
    assert values["state"] == "open"
    assert values["author"] == "renovate[bot]"
    assert values["author_is_maintainer"] is True
    assert values["author_is_bot"] is True
    assert values["labels"] == ["bug", "snapcraft"]
    assert values["created_at"] == datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    assert values["updated_at"] == datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
    assert values["closed_at"] is None
    assert values["url"] == "https://github.com/canonical/repo/issues/123"
    assert values["metadata_"] == metadata
    assert values["comments"] == comments
    assert values["content_hash"] == compute_content_hash(
        "snapcraft pack fails",
        "Build fails during prime.",
        "open",
        ["bug", "snapcraft"],
        comments,
        pr_details=metadata,
    )
    assert before <= values["last_fetched_at"] <= after


async def test_collection_watermark_helpers_store_rows_by_project_and_source(
    test_db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(collect_projects, "insert", sqlite_insert)
    project = make_project(name="snapcraft")
    test_db_session.add(project)
    await test_db_session.commit()
    collected_at = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    open_collected_at = datetime(2025, 1, 1, 13, 0, tzinfo=UTC)

    await collect_projects._upsert_collection_watermark(
        test_db_session, project.id, "github", collected_at
    )
    await collect_projects._upsert_collection_watermark(
        test_db_session, project.id, "github_issues_open", open_collected_at
    )

    assert await collect_projects._get_collection_watermark(
        test_db_session, project.id, "github"
    ) == collected_at.replace(tzinfo=None)
    assert await collect_projects._get_collection_watermark(
        test_db_session, project.id, "github_issues_open"
    ) == open_collected_at.replace(tzinfo=None)
    assert (
        await collect_projects._get_collection_watermark(
            test_db_session, project.id, "launchpad"
        )
        is None
    )


async def test_collection_watermark_upsert_replaces_only_matching_source(
    test_db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(collect_projects, "insert", sqlite_insert)
    project = make_project(name="charmcraft")
    test_db_session.add(project)
    await test_db_session.commit()
    first = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    second = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
    open_value = datetime(2025, 1, 3, 12, 0, tzinfo=UTC)

    await collect_projects._upsert_collection_watermark(
        test_db_session, project.id, "github", first
    )
    await collect_projects._upsert_collection_watermark(
        test_db_session, project.id, "github_issues_open", open_value
    )
    await collect_projects._upsert_collection_watermark(
        test_db_session, project.id, "github", second
    )

    rows = (
        await test_db_session.execute(
            sa.select(
                CollectionWatermark.source,
                CollectionWatermark.last_collected_at,
            ).order_by(CollectionWatermark.source)
        )
    ).all()
    assert rows == [
        ("github", second.replace(tzinfo=None)),
        ("github_issues_open", open_value.replace(tzinfo=None)),
    ]


async def test_open_collection_reads_open_watermark_and_writes_start_time_on_success(
    monkeypatch,
) -> None:
    watermark = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    session = MagicMock()
    collector = MagicMock()
    collector.check_rate_limit.return_value = {
        "core_remaining": 1000,
        "core_limit": 5000,
        "core_reset": None,
    }
    collector.collect_releases = AsyncMock(return_value=0)
    collector.collect_issues = AsyncMock(return_value=3)
    monkeypatch.setattr(
        github_pass, "GitHubCollector", MagicMock(return_value=collector)
    )
    dep_collector = MagicMock()
    dep_collector.collect_dependencies = AsyncMock(return_value=0)
    monkeypatch.setattr(
        github_pass, "DependencyCollector", MagicMock(return_value=dep_collector)
    )
    monkeypatch.setattr(github_pass, "_get_dep_branches", MagicMock(return_value=[]))
    monkeypatch.setattr(
        github_pass, "_get_or_create_project", AsyncMock(return_value=9)
    )
    monkeypatch.setattr(
        github_pass, "_get_collection_watermark", AsyncMock(return_value=watermark)
    )
    upsert_watermark = AsyncMock()
    monkeypatch.setattr(github_pass, "_upsert_collection_watermark", upsert_watermark)
    monkeypatch.setattr(github_pass, "generate_snapshot", AsyncMock())
    monkeypatch.setattr(github_pass, "record_open_poll_success", AsyncMock())
    settings = SimpleNamespace(github_token=_TEST_TOKEN, refresh_age_days=7)
    config = SimpleNamespace(
        maintainers=[],
        bots=[],
        craft_projects=["snapcraft"],
        craft_applications=["snapcraft"],
        craft_libraries=[],
        hotfix_min_versions={},
        filtered_issues={},
    )

    stats = await github_pass._collect_github(
        settings,
        config,
        lambda: _SessionContext(session),
        mode="open",
        collection_run_id=42,
    )

    collector.collect_issues.assert_awaited_once_with(
        "snapcraft",
        9,
        session,
        limit=0,
        state="open",
        since=watermark,
        collection_run_id=42,
    )
    upsert_watermark.assert_awaited_once()
    assert upsert_watermark.await_args.args[:3] == (
        session,
        9,
        "github_issues_open",
    )
    written_at = upsert_watermark.await_args.args[3]
    assert isinstance(written_at, datetime)
    assert stats.issues_collected == 3


async def test_open_watermark_not_advanced_when_collect_issues_raises(
    monkeypatch,
) -> None:
    watermark = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    session = MagicMock()
    collector = MagicMock()
    collector.check_rate_limit.return_value = {
        "core_remaining": 1000,
        "core_limit": 5000,
        "core_reset": None,
    }
    collector.collect_releases = AsyncMock(return_value=0)
    collector.collect_issues = AsyncMock(side_effect=RuntimeError("page fetch failed"))
    monkeypatch.setattr(
        github_pass, "GitHubCollector", MagicMock(return_value=collector)
    )
    dep_collector = MagicMock()
    dep_collector.collect_dependencies = AsyncMock(return_value=0)
    monkeypatch.setattr(
        github_pass, "DependencyCollector", MagicMock(return_value=dep_collector)
    )
    monkeypatch.setattr(github_pass, "_get_dep_branches", MagicMock(return_value=[]))
    monkeypatch.setattr(
        github_pass, "_get_or_create_project", AsyncMock(return_value=9)
    )
    monkeypatch.setattr(
        github_pass, "_get_collection_watermark", AsyncMock(return_value=watermark)
    )
    upsert_watermark = AsyncMock()
    monkeypatch.setattr(github_pass, "_upsert_collection_watermark", upsert_watermark)
    monkeypatch.setattr(github_pass, "record_refresh_error", AsyncMock())
    settings = SimpleNamespace(github_token=_TEST_TOKEN, refresh_age_days=7)
    config = SimpleNamespace(
        maintainers=[],
        bots=[],
        craft_projects=["snapcraft"],
        craft_applications=["snapcraft"],
        craft_libraries=[],
        hotfix_min_versions={},
        filtered_issues={},
    )

    stats = await github_pass._collect_github(
        settings,
        config,
        lambda: _SessionContext(session),
        mode="open",
        collection_run_id=42,
    )

    collector.collect_issues.assert_awaited_once()
    upsert_watermark.assert_not_awaited()
    assert stats.issues_collected == 0
    assert stats.errors == [{"project": "snapcraft", "error": "page fetch failed"}]


async def test_collect_issues_does_not_commit_when_iterator_raises_after_staging_item(
    mocker,
) -> None:
    def raising_issue_iterator():
        yield _make_rest_issue()
        raise RuntimeError("second page failed")

    collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
    repo = MagicMock()
    repo.get_issues.return_value = raising_issue_iterator()
    collector.gh = MagicMock()
    collector.gh.get_repo.return_value = repo
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _count_result(1),
            _count_result(1),
            _count_result(1),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            _no_existing_issue_result(),
            None,
        ]
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    mocker.patch(
        "craft_dashboard.collectors.github._fetch_issue_comments", return_value=[]
    )
    statements = []

    def recording_insert(_table):
        stmt = _fake_insert(_table)
        statements.append(stmt)
        return stmt

    mocker.patch("sqlalchemy.dialects.postgresql.insert", side_effect=recording_insert)

    with pytest.raises(RuntimeError, match="second page failed"):
        await collector.collect_issues("repo", 1, session, state="all")

    # NOTE: looks suspect -- one issue/activity is staged before the page error,
    # but collect_issues itself does not commit it. The caller's session
    # handling decides whether that staged partial work is rolled back.
    assert len(statements) == 1
    assert statements[0].values_kwargs["external_id"] == "123"
    session.add.assert_called_once()
    session.commit.assert_not_awaited()


async def test_collect_issues_uses_explicit_since_as_rest_watermark(mocker) -> None:
    since = datetime(2025, 1, 5, 12, 0)
    collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
    repo = MagicMock()
    repo.get_issues.return_value = [_make_rest_issue()]
    collector.gh = MagicMock()
    collector.gh.get_repo.return_value = repo
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _count_result(1),
            _count_result(1),
            _count_result(1),
            _no_existing_issue_result(),
            None,
        ]
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    mocker.patch(
        "craft_dashboard.collectors.github._fetch_issue_comments", return_value=[]
    )
    mocker.patch("sqlalchemy.dialects.postgresql.insert", side_effect=_fake_insert)

    count = await collector.collect_issues("repo", 1, session, state="all", since=since)

    assert count == 1
    repo.get_issues.assert_called_once_with(
        state="all",
        sort="updated",
        direction="desc",
        since=datetime(2025, 1, 5, 12, 0, tzinfo=UTC),
    )
    session.commit.assert_awaited_once()


async def test_collect_issues_derives_rest_since_from_oldest_due_last_fetched(
    mocker,
) -> None:
    oldest = datetime(2025, 1, 5, 12, 0, tzinfo=UTC)
    collector = GitHubCollector(token=_TEST_TOKEN, org="canonical")
    repo = MagicMock()
    repo.get_issues.return_value = []
    collector.gh = MagicMock()
    collector.gh.get_repo.return_value = repo
    oldest_result = MagicMock()
    oldest_result.scalar_one_or_none.return_value = oldest
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _count_result(1),
            _count_result(1),
            _count_result(1),
            oldest_result,
        ]
    )
    session.commit = AsyncMock()
    datetime_mock = mocker.patch("craft_dashboard.collectors.github.datetime")
    datetime_mock.now.return_value = datetime(2025, 2, 1, 12, 0, tzinfo=UTC)
    datetime_mock.side_effect = datetime

    count = await collector.collect_issues("repo", 1, session, state="all")

    assert count == 0
    repo.get_issues.assert_called_once_with(
        state="all",
        sort="updated",
        direction="desc",
        since=oldest - timedelta(days=1),
    )
    session.commit.assert_awaited_once()
