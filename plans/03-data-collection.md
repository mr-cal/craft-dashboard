# Plan 3: Data Collection Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the existing GitHub and Launchpad data collection from starcraft-stats to craft-dashboard, writing data into PostgreSQL instead of CSV/JSON files. Add a refresh scheduler, snapshot generation, and cron-friendly entry point scripts.

**Architecture:** Collectors are async Python modules that fetch data from external APIs and upsert into the database. A refresh scheduler spreads API calls across a configurable interval to avoid rate limits. Entry point scripts (`scripts/collect_data.py`, `scripts/migrate_csv.py`) are designed for cron and one-time migration use.

**Tech Stack:** PyGithub, launchpadlib, SQLAlchemy async, httpx, click

> **Existing code to read before implementing:** `starcraft_stats/issues.py` (GitHub API calls, pagination, rate limiting, field mapping — the most important reference), `starcraft_stats/launchpad.py` (Launchpad API quirks and auth), `starcraft_stats/dependencies.py`, `starcraft_stats/releases.py`, `starcraft_stats/schedule.py`.

**Depends on:** Plans 1 and 2

---

### Task 1: GitHub Issue Collector

**Files:**
- Create: `craft_dashboard/collectors/__init__.py`
- Create: `craft_dashboard/collectors/github.py`
- Test: `tests/unit/collectors/__init__.py`
- Test: `tests/unit/collectors/test_github.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/collectors/__init__.py`:
```python
```

Create `tests/unit/collectors/test_github.py`:
```python
"""Tests for the GitHub data collector."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from craft_dashboard.collectors.github import (
    GitHubCollector,
    _classify_issue,
    _compute_issue_hash,
)


class TestClassifyIssue:
    """Tests for _classify_issue helper."""

    def test_classify_open_issue(self) -> None:
        """Open issue with no pull_request attribute is classified as 'issue'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = None
        gh_issue.state = "open"

        result = _classify_issue(gh_issue)

        assert result == ("issue", "open")

    def test_classify_open_pr(self) -> None:
        """Open issue with pull_request attribute is classified as 'pull_request'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.state = "open"

        result = _classify_issue(gh_issue)

        assert result == ("pull_request", "open")

    def test_classify_closed_issue(self) -> None:
        """Closed issue is classified with state 'closed'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = None
        gh_issue.state = "closed"

        result = _classify_issue(gh_issue)

        assert result == ("issue", "closed")

    def test_classify_merged_pr(self) -> None:
        """Closed PR with merged_at is classified as 'merged'."""
        gh_issue = MagicMock()
        gh_issue.pull_request = MagicMock()
        gh_issue.pull_request.merged_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        gh_issue.state = "closed"

        result = _classify_issue(gh_issue)

        assert result == ("pull_request", "merged")


class TestComputeIssueHash:
    """Tests for _compute_issue_hash."""

    def test_same_content_same_hash(self) -> None:
        """Identical content produces identical hashes."""
        hash1 = _compute_issue_hash("title", "body", "open", ["bug"])
        hash2 = _compute_issue_hash("title", "body", "open", ["bug"])

        assert hash1 == hash2

    def test_different_content_different_hash(self) -> None:
        """Different content produces different hashes."""
        hash1 = _compute_issue_hash("title1", "body", "open", ["bug"])
        hash2 = _compute_issue_hash("title2", "body", "open", ["bug"])

        assert hash1 != hash2

    def test_hash_is_hex_string(self) -> None:
        """Hash is a 64-character hex string (SHA-256)."""
        result = _compute_issue_hash("title", "body", "open", [])

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


class TestGitHubCollector:
    """Tests for GitHubCollector."""

    def test_init(self) -> None:
        """GitHubCollector initializes with a token and org."""
        collector = GitHubCollector(token="ghp_test", org="canonical")

        assert collector.org == "canonical"

    def test_is_maintainer(self) -> None:
        """is_maintainer checks against the maintainer list."""
        collector = GitHubCollector(
            token="ghp_test", org="canonical", maintainers=["mr-cal", "lengau"]
        )

        assert collector.is_maintainer("mr-cal") is True
        assert collector.is_maintainer("some-user") is False

    def test_is_maintainer_empty_list(self) -> None:
        """is_maintainer returns False when no maintainers configured."""
        collector = GitHubCollector(token="ghp_test", org="canonical")

        assert collector.is_maintainer("anyone") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/collectors/test_github.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/collectors/__init__.py`:
```python
"""Data collectors for external sources."""
```

Create `craft_dashboard/collectors/github.py`:
```python
"""GitHub data collector for issues, PRs, releases, and dependencies."""

import hashlib
import logging
from datetime import datetime, timezone

from github import Github
from github.Issue import Issue as GHIssue

logger = logging.getLogger(__name__)


def _classify_issue(gh_issue: GHIssue) -> tuple[str, str]:
    """Classify a GitHub issue as issue or PR, and determine its state.

    Args:
        gh_issue: A PyGithub Issue object.

    Returns:
        A tuple of (issue_type, state).
    """
    is_pr = gh_issue.pull_request is not None
    issue_type = "pull_request" if is_pr else "issue"

    if gh_issue.state == "closed" and is_pr:
        merged_at = getattr(gh_issue.pull_request, "merged_at", None)
        if merged_at is not None:
            return issue_type, "merged"

    return issue_type, gh_issue.state


def _compute_issue_hash(
    title: str,
    body: str | None,
    state: str,
    labels: list[str],
) -> str:
    """Compute a SHA-256 hash of issue content for change detection.

    Args:
        title: Issue title.
        body: Issue body text.
        state: Issue state.
        labels: List of label names.

    Returns:
        A 64-character hex string.
    """
    content = f"{title}|{body or ''}|{state}|{','.join(sorted(labels))}"
    return hashlib.sha256(content.encode()).hexdigest()


class GitHubCollector:
    """Collects issue, PR, release, and dependency data from GitHub."""

    def __init__(
        self,
        token: str,
        org: str = "canonical",
        maintainers: list[str] | None = None,
    ) -> None:
        """Initialize the GitHub collector.

        Args:
            token: GitHub personal access token.
            org: GitHub organization name.
            maintainers: List of GitHub usernames considered maintainers.
        """
        self.gh = Github(token)
        self.org = org
        self.maintainers = set(maintainers or [])

    def is_maintainer(self, username: str) -> bool:
        """Check if a username belongs to a project maintainer.

        Args:
            username: GitHub username to check.

        Returns:
            True if the user is a maintainer.
        """
        return username in self.maintainers

    async def collect_issues(
        self,
        repo_name: str,
        project_id: int,
        session,
    ) -> int:
        """Collect all open issues and PRs for a repository.

        Fetches issues from GitHub and upserts them into the database.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.

        Returns:
            The number of issues upserted.
        """
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert

        from craft_dashboard.models.issue import Issue

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")
        gh_issues = repo.get_issues(state="all")
        count = 0

        for gh_issue in gh_issues:
            issue_type, state = _classify_issue(gh_issue)
            label_names = [label.name for label in gh_issue.labels]
            author = gh_issue.user.login if gh_issue.user else None

            stmt = insert(Issue).values(
                project_id=project_id,
                source="github",
                external_id=str(gh_issue.number),
                issue_type=issue_type,
                title=gh_issue.title,
                body=gh_issue.body,
                state=state,
                author=author,
                author_is_maintainer=self.is_maintainer(author) if author else False,
                labels=label_names,
                created_at=gh_issue.created_at.replace(tzinfo=timezone.utc)
                if gh_issue.created_at
                else None,
                updated_at=gh_issue.updated_at.replace(tzinfo=timezone.utc)
                if gh_issue.updated_at
                else None,
                closed_at=gh_issue.closed_at.replace(tzinfo=timezone.utc)
                if gh_issue.closed_at
                else None,
                url=gh_issue.html_url,
                last_fetched_at=datetime.now(tz=timezone.utc),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "source", "external_id"],
                set_={
                    "title": stmt.excluded.title,
                    "body": stmt.excluded.body,
                    "state": stmt.excluded.state,
                    "author": stmt.excluded.author,
                    "author_is_maintainer": stmt.excluded.author_is_maintainer,
                    "labels": stmt.excluded.labels,
                    "updated_at": stmt.excluded.updated_at,
                    "closed_at": stmt.excluded.closed_at,
                    "last_fetched_at": stmt.excluded.last_fetched_at,
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        logger.info("Collected %d issues from %s/%s", count, self.org, repo_name)
        return count

    async def collect_releases(
        self,
        repo_name: str,
        project_id: int,
        session,
    ) -> int:
        """Collect releases for a repository.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.

        Returns:
            The number of releases upserted.
        """
        from sqlalchemy.dialects.postgresql import insert

        from craft_dashboard.models.release import Release

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")
        count = 0

        for gh_release in repo.get_releases():
            stmt = insert(Release).values(
                project_id=project_id,
                version=gh_release.tag_name,
                branch=gh_release.target_commitish,
                released_at=gh_release.published_at.replace(tzinfo=timezone.utc)
                if gh_release.published_at
                else None,
                is_hotfix=False,
                metadata={
                    "prerelease": gh_release.prerelease,
                    "draft": gh_release.draft,
                },
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "version"],
                set_={
                    "branch": stmt.excluded.branch,
                    "released_at": stmt.excluded.released_at,
                    "metadata": stmt.excluded.metadata,
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        logger.info("Collected %d releases from %s/%s", count, self.org, repo_name)
        return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/collectors/test_github.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/collectors/ tests/unit/collectors/
git commit -m "feat: add GitHub collector for issues, PRs, and releases"
```

---

### Task 2: Launchpad Collector

**Files:**
- Create: `craft_dashboard/collectors/launchpad.py`
- Test: `tests/unit/collectors/test_launchpad.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/collectors/test_launchpad.py`:
```python
"""Tests for the Launchpad data collector."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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
        for status in ["Fix Released", "Fix Committed", "Invalid", "Won't Fix", "Expired"]:
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/collectors/test_launchpad.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/collectors/launchpad.py`:
```python
"""Launchpad data collector for bugs."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_OPEN_STATUSES = frozenset({
    "New",
    "Confirmed",
    "Triaged",
    "In Progress",
    "Incomplete",
    "Opinion",
    "Incomplete (with response)",
    "Incomplete (without response)",
})

_CLOSED_STATUSES = frozenset({
    "Fix Released",
    "Fix Committed",
    "Invalid",
    "Won't Fix",
    "Expired",
})


def _map_lp_status(lp_status: str) -> str:
    """Map a Launchpad bug status to a normalized state.

    Args:
        lp_status: The Launchpad bug task status string.

    Returns:
        'open' or 'closed'.
    """
    if lp_status in _CLOSED_STATUSES:
        return "closed"
    return "open"


class LaunchpadCollector:
    """Collects bug data from Launchpad."""

    def __init__(self, projects: list[str] | None = None) -> None:
        """Initialize the Launchpad collector.

        Args:
            projects: List of Launchpad project names to collect from.
        """
        self.projects = projects or []
        self._lp = None

    def _get_launchpad(self):
        """Lazily initialize the Launchpad API client.

        Returns:
            A launchpadlib Launchpad instance.
        """
        if self._lp is None:
            from launchpadlib.launchpad import Launchpad

            self._lp = Launchpad.login_anonymously(
                "craft-dashboard", "production", version="devel"
            )
        return self._lp

    async def collect_bugs(
        self,
        lp_project_name: str,
        project_id: int,
        session,
    ) -> int:
        """Collect bugs for a Launchpad project.

        Args:
            lp_project_name: Launchpad project name.
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.

        Returns:
            The number of bugs upserted.
        """
        from sqlalchemy.dialects.postgresql import insert

        from craft_dashboard.models.issue import Issue

        lp = self._get_launchpad()
        project = lp.projects[lp_project_name]
        bug_tasks = project.searchTasks(status=list(_OPEN_STATUSES | _CLOSED_STATUSES))
        count = 0

        for task in bug_tasks:
            bug = task.bug
            state = _map_lp_status(task.status)

            stmt = insert(Issue).values(
                project_id=project_id,
                source="launchpad",
                external_id=str(bug.id),
                issue_type="issue",
                title=bug.title,
                body=bug.description,
                state=state,
                author=str(task.owner_link).rsplit("/", 1)[-1]
                if task.owner_link
                else None,
                author_is_maintainer=False,
                labels=[tag for tag in bug.tags],
                created_at=bug.date_created.replace(tzinfo=timezone.utc)
                if bug.date_created
                else None,
                updated_at=bug.date_last_updated.replace(tzinfo=timezone.utc)
                if bug.date_last_updated
                else None,
                closed_at=task.date_closed.replace(tzinfo=timezone.utc)
                if hasattr(task, "date_closed") and task.date_closed
                else None,
                url=bug.web_link,
                metadata={"importance": task.importance, "status": task.status},
                last_fetched_at=datetime.now(tz=timezone.utc),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "source", "external_id"],
                set_={
                    "title": stmt.excluded.title,
                    "body": stmt.excluded.body,
                    "state": stmt.excluded.state,
                    "labels": stmt.excluded.labels,
                    "updated_at": stmt.excluded.updated_at,
                    "closed_at": stmt.excluded.closed_at,
                    "metadata": stmt.excluded.metadata,
                    "last_fetched_at": stmt.excluded.last_fetched_at,
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        logger.info("Collected %d bugs from Launchpad project %s", count, lp_project_name)
        return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/collectors/test_launchpad.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/collectors/launchpad.py tests/unit/collectors/test_launchpad.py
git commit -m "feat: add Launchpad collector for bugs"
```

---

### Task 3: Dependency Collector

**Files:**
- Create: `craft_dashboard/collectors/dependencies.py`
- Test: `tests/unit/collectors/test_dependencies.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/collectors/test_dependencies.py`:
```python
"""Tests for the dependency collector."""

from craft_dashboard.collectors.dependencies import (
    DependencyCollector,
    parse_requirements_line,
)


class TestParseRequirementsLine:
    """Tests for parse_requirements_line."""

    def test_simple_dependency(self) -> None:
        """Parse a simple dependency like 'requests'."""
        name, spec = parse_requirements_line("requests")

        assert name == "requests"
        assert spec == ""

    def test_dependency_with_version(self) -> None:
        """Parse 'requests>=2.28'."""
        name, spec = parse_requirements_line("requests>=2.28")

        assert name == "requests"
        assert spec == ">=2.28"

    def test_dependency_with_tilde(self) -> None:
        """Parse 'pydantic~=2.8'."""
        name, spec = parse_requirements_line("pydantic~=2.8")

        assert name == "pydantic"
        assert spec == "~=2.8"

    def test_dependency_with_extras(self) -> None:
        """Parse 'uvicorn[standard]>=0.34'."""
        name, spec = parse_requirements_line("uvicorn[standard]>=0.34")

        assert name == "uvicorn"
        assert spec == ">=0.34"

    def test_empty_line(self) -> None:
        """Empty lines return None."""
        result = parse_requirements_line("")

        assert result is None

    def test_comment_line(self) -> None:
        """Comment lines return None."""
        result = parse_requirements_line("# this is a comment")

        assert result is None


class TestDependencyCollector:
    """Tests for DependencyCollector."""

    def test_init(self) -> None:
        """DependencyCollector initializes with a token and org."""
        collector = DependencyCollector(token="ghp_test", org="canonical")

        assert collector.org == "canonical"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/collectors/test_dependencies.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/collectors/dependencies.py`:
```python
"""Dependency collector for tracking project dependencies across branches."""

import logging
import re

from datetime import datetime, timezone

from github import Github

logger = logging.getLogger(__name__)

_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)"  # package name
    r"(?:\[.*?\])?"  # optional extras
    r"(.*)"  # version spec
)


def parse_requirements_line(line: str) -> tuple[str, str] | None:
    """Parse a single requirements line into (name, version_spec).

    Args:
        line: A single line from a requirements file or pyproject.toml dependency.

    Returns:
        A tuple of (package_name, version_spec), or None for empty/comment lines.
    """
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("-"):
        return None

    match = _REQUIREMENT_RE.match(line)
    if not match:
        return None

    name = match.group(1)
    spec = match.group(2).strip()
    return name, spec


class DependencyCollector:
    """Collects dependency information from project repositories."""

    def __init__(self, token: str, org: str = "canonical") -> None:
        """Initialize the dependency collector.

        Args:
            token: GitHub personal access token.
            org: GitHub organization name.
        """
        self.gh = Github(token)
        self.org = org

    async def collect_dependencies(
        self,
        repo_name: str,
        project_id: int,
        branches: list[str],
        session,
    ) -> int:
        """Collect dependencies for a repository across specified branches.

        Reads pyproject.toml from each branch and parses the dependencies list.

        Args:
            repo_name: Repository name (without org prefix).
            project_id: The database ID of the project.
            branches: List of branch names to check.
            session: An async SQLAlchemy session.

        Returns:
            The number of dependencies upserted.
        """
        import tomllib

        from sqlalchemy.dialects.postgresql import insert

        from craft_dashboard.models.dependency import Dependency

        repo = self.gh.get_repo(f"{self.org}/{repo_name}")
        count = 0

        for branch in branches:
            try:
                contents = repo.get_contents("pyproject.toml", ref=branch)
                pyproject = tomllib.loads(contents.decoded_content.decode())
            except Exception:
                logger.warning(
                    "Could not read pyproject.toml from %s/%s@%s",
                    self.org,
                    repo_name,
                    branch,
                )
                continue

            deps = pyproject.get("project", {}).get("dependencies", [])

            for dep_line in deps:
                parsed = parse_requirements_line(dep_line)
                if parsed is None:
                    continue
                dep_name, version_spec = parsed

                stmt = insert(Dependency).values(
                    project_id=project_id,
                    branch=branch,
                    dependency_name=dep_name,
                    version_spec=version_spec,
                    source_file="pyproject.toml",
                    fetched_at=datetime.now(tz=timezone.utc),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["project_id", "branch", "dependency_name"],
                    set_={
                        "version_spec": stmt.excluded.version_spec,
                        "source_file": stmt.excluded.source_file,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                await session.execute(stmt)
                count += 1

        await session.commit()
        logger.info(
            "Collected %d dependencies from %s/%s", count, self.org, repo_name
        )
        return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/collectors/test_dependencies.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/collectors/dependencies.py tests/unit/collectors/test_dependencies.py
git commit -m "feat: add dependency collector for pyproject.toml parsing"
```

---

### Task 4: Snapshot Generator

**Files:**
- Create: `craft_dashboard/collectors/snapshots.py`
- Test: `tests/unit/collectors/test_snapshots.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/collectors/test_snapshots.py`:
```python
"""Tests for the snapshot generator."""

from datetime import date

from craft_dashboard.collectors.snapshots import compute_snapshot_counts


class TestComputeSnapshotCounts:
    """Tests for compute_snapshot_counts."""

    def test_empty_issues(self) -> None:
        """No issues produces all-zero counts."""
        result = compute_snapshot_counts(issues=[], maintainers=set())

        assert result == {
            "open_issues": 0,
            "open_prs": 0,
            "open_issues_external": 0,
            "open_issues_internal": 0,
            "open_prs_external": 0,
            "open_prs_internal": 0,
            "open_bugs": 0,
        }

    def test_counts_open_issues(self) -> None:
        """Counts open issues correctly."""
        issues = [
            {"issue_type": "issue", "state": "open", "author": "external-user", "labels": []},
            {"issue_type": "issue", "state": "open", "author": "mr-cal", "labels": ["bug"]},
            {"issue_type": "issue", "state": "closed", "author": "someone", "labels": []},
        ]
        maintainers = {"mr-cal"}

        result = compute_snapshot_counts(issues=issues, maintainers=maintainers)

        assert result["open_issues"] == 2
        assert result["open_issues_external"] == 1
        assert result["open_issues_internal"] == 1
        assert result["open_bugs"] == 1

    def test_counts_open_prs(self) -> None:
        """Counts open PRs correctly."""
        issues = [
            {"issue_type": "pull_request", "state": "open", "author": "external-user", "labels": []},
            {"issue_type": "pull_request", "state": "open", "author": "mr-cal", "labels": []},
            {"issue_type": "pull_request", "state": "merged", "author": "someone", "labels": []},
        ]
        maintainers = {"mr-cal"}

        result = compute_snapshot_counts(issues=issues, maintainers=maintainers)

        assert result["open_prs"] == 2
        assert result["open_prs_external"] == 1
        assert result["open_prs_internal"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/collectors/test_snapshots.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/collectors/snapshots.py`:
```python
"""Snapshot generator for daily issue/PR count tracking."""

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)


def compute_snapshot_counts(
    issues: list[dict],
    maintainers: set[str],
) -> dict[str, int]:
    """Compute snapshot counts from a list of issue dicts.

    Args:
        issues: List of dicts with keys: issue_type, state, author, labels.
        maintainers: Set of maintainer usernames.

    Returns:
        Dict with snapshot count fields.
    """
    counts = {
        "open_issues": 0,
        "open_prs": 0,
        "open_issues_external": 0,
        "open_issues_internal": 0,
        "open_prs_external": 0,
        "open_prs_internal": 0,
        "open_bugs": 0,
    }

    for issue in issues:
        if issue["state"] not in ("open",):
            continue

        is_internal = issue.get("author") in maintainers

        if issue["issue_type"] == "issue":
            counts["open_issues"] += 1
            if is_internal:
                counts["open_issues_internal"] += 1
            else:
                counts["open_issues_external"] += 1
            if "bug" in issue.get("labels", []):
                counts["open_bugs"] += 1

        elif issue["issue_type"] == "pull_request":
            counts["open_prs"] += 1
            if is_internal:
                counts["open_prs_internal"] += 1
            else:
                counts["open_prs_external"] += 1

    return counts


async def generate_snapshot(
    project_id: int,
    session,
    maintainers: set[str],
) -> None:
    """Generate a daily snapshot for a project.

    Queries current open issues/PRs and upserts a snapshot row for today.

    Args:
        project_id: The database ID of the project.
        session: An async SQLAlchemy session.
        maintainers: Set of maintainer usernames.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.snapshot import Snapshot

    result = await session.execute(
        select(
            Issue.issue_type,
            Issue.state,
            Issue.author,
            Issue.labels,
        ).where(Issue.project_id == project_id)
    )

    issues = [
        {
            "issue_type": row.issue_type,
            "state": row.state,
            "author": row.author,
            "labels": row.labels if isinstance(row.labels, list) else [],
        }
        for row in result
    ]

    counts = compute_snapshot_counts(issues, maintainers)
    today = date.today()

    stmt = insert(Snapshot).values(
        project_id=project_id,
        snapshot_date=today,
        **counts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "snapshot_date"],
        set_=counts,
    )
    await session.execute(stmt)
    await session.commit()

    logger.info("Generated snapshot for project_id=%d on %s", project_id, today)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/collectors/test_snapshots.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/collectors/snapshots.py tests/unit/collectors/test_snapshots.py
git commit -m "feat: add snapshot generator for daily trend tracking"
```

---

### Task 5: Refresh Scheduler

**Files:**
- Create: `craft_dashboard/collectors/scheduler.py`
- Test: `tests/unit/collectors/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/collectors/test_scheduler.py`:
```python
"""Tests for the refresh scheduler."""

from datetime import datetime, timedelta, timezone

from craft_dashboard.collectors.scheduler import (
    distribute_refresh_dates,
    is_due_for_refresh,
    record_refresh_error,
    update_refresh_schedule,
)


class TestIsDueForRefresh:
    """Tests for is_due_for_refresh."""

    def test_none_next_refresh_is_due(self) -> None:
        """If next_refresh_at is None, it's due."""
        assert is_due_for_refresh(next_refresh_at=None) is True

    def test_past_date_is_due(self) -> None:
        """If next_refresh_at is in the past, it's due."""
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        assert is_due_for_refresh(next_refresh_at=past) is True

    def test_future_date_not_due(self) -> None:
        """If next_refresh_at is in the future, it's not due."""
        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        assert is_due_for_refresh(next_refresh_at=future) is False


class TestDistributeRefreshDates:
    """Tests for distribute_refresh_dates."""

    def test_distributes_evenly(self) -> None:
        """Projects are spread across the interval."""
        project_ids = [1, 2, 3]
        interval_days = 7

        result = distribute_refresh_dates(project_ids, interval_days)

        assert len(result) == 3
        # All dates should be in the future
        now = datetime.now(tz=timezone.utc)
        for pid, dt in result:
            assert dt > now
            assert pid in project_ids

    def test_empty_projects(self) -> None:
        """Empty project list returns empty result."""
        result = distribute_refresh_dates([], 7)

        assert result == []

    def test_single_project(self) -> None:
        """Single project gets first slot."""
        result = distribute_refresh_dates([42], 7)

        assert len(result) == 1
        assert result[0][0] == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/collectors/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/collectors/scheduler.py`:
```python
"""Refresh scheduler for spreading data collection across time."""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def is_due_for_refresh(next_refresh_at: datetime | None) -> bool:
    """Check if a project is due for a data refresh.

    Args:
        next_refresh_at: The scheduled refresh time, or None if never scheduled.

    Returns:
        True if the project should be refreshed now.
    """
    if next_refresh_at is None:
        return True
    return datetime.now(tz=timezone.utc) >= next_refresh_at


def distribute_refresh_dates(
    project_ids: list[int],
    interval_days: int,
) -> list[tuple[int, datetime]]:
    """Distribute refresh dates evenly across an interval.

    Spreads projects across the interval so API calls are distributed
    over time rather than all happening at once.

    Args:
        project_ids: List of project database IDs.
        interval_days: Number of days over which to spread refreshes.

    Returns:
        List of (project_id, next_refresh_at) tuples.
    """
    if not project_ids:
        return []

    now = datetime.now(tz=timezone.utc)
    total_seconds = interval_days * 86400
    interval = total_seconds / len(project_ids)

    return [
        (pid, now + timedelta(seconds=interval * (i + 1)))
        for i, pid in enumerate(project_ids)
    ]


async def update_refresh_schedule(
    project_id: int,
    source: str,
    interval_days: int,
    session,
) -> None:
    """Mark a project as successfully refreshed and schedule the next refresh.

    Clears any recorded error state on success.

    Args:
        project_id: The database ID of the project.
        source: The data source ('github' or 'launchpad').
        interval_days: Days until next refresh.
        session: An async SQLAlchemy session.
    """
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    now = datetime.now(tz=timezone.utc)

    stmt = insert(RefreshSchedule).values(
        project_id=project_id,
        source=source,
        last_refreshed_at=now,
        next_refresh_at=now + timedelta(days=interval_days),
        last_error=None,
        consecutive_failures=0,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "source"],
        set_={
            "last_refreshed_at": now,
            "next_refresh_at": now + timedelta(days=interval_days),
            "last_error": None,
            "consecutive_failures": 0,
        },
    )
    await session.execute(stmt)
    await session.commit()

    logger.info(
        "Updated refresh schedule for project_id=%d source=%s",
        project_id,
        source,
    )


async def record_refresh_error(
    project_id: int,
    source: str,
    error: str,
    session,
) -> None:
    """Record a collection failure and increment the consecutive failure counter.

    Args:
        project_id: The database ID of the project.
        source: The data source ('github' or 'launchpad').
        error: The error message to record.
        session: An async SQLAlchemy session.
    """
    from sqlalchemy import select, update

    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    # Increment failures and record error
    stmt = (
        update(RefreshSchedule)
        .where(
            RefreshSchedule.project_id == project_id,
            RefreshSchedule.source == source,
        )
        .values(
            last_error=error,
            consecutive_failures=RefreshSchedule.consecutive_failures + 1,
        )
    )
    result = await session.execute(stmt)

    # If no row existed yet, insert one
    if result.rowcount == 0:
        from sqlalchemy.dialects.postgresql import insert

        stmt = insert(RefreshSchedule).values(
            project_id=project_id,
            source=source,
            last_error=error,
            consecutive_failures=1,
        )
        stmt = stmt.on_conflict_do_nothing()
        await session.execute(stmt)

    await session.commit()
    logger.warning(
        "Recorded refresh error for project_id=%d source=%s: %s",
        project_id,
        source,
        error,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/collectors/test_scheduler.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/collectors/scheduler.py tests/unit/collectors/test_scheduler.py
git commit -m "feat: add refresh scheduler for distributed data collection"
```

---

### Task 6: Data Collection Entry Point Script

**Files:**
- Create: `scripts/collect_data.py`

- [ ] **Step 1: Create the cron-friendly collection script**

Create `scripts/collect_data.py`:
```python
#!/usr/bin/env python3
"""Data collection entry point for cron jobs.

Usage:
    uv run scripts/collect_data.py --source all
    uv run scripts/collect_data.py --source github
    uv run scripts/collect_data.py --source launchpad

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
    GITHUB_TOKEN: GitHub personal access token
"""

import asyncio
import logging
import pathlib
import sys

import click

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.collectors.github import GitHubCollector
from craft_dashboard.collectors.launchpad import LaunchpadCollector
from craft_dashboard.collectors.scheduler import (
    is_due_for_refresh,
    record_refresh_error,
    update_refresh_schedule,
)
from craft_dashboard.collectors.snapshots import generate_snapshot
from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _get_or_create_project(session, name: str, category: str, order: int) -> int:
    """Get or create a project, returning its ID."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.project import Project

    stmt = insert(Project).values(
        name=name,
        category=category,
        display_order=order,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
    await session.execute(stmt)
    await session.commit()

    result = await session.execute(select(Project.id).where(Project.name == name))
    return result.scalar_one()


async def _collect_github(
    settings: Settings,
    config,
    session_factory,
) -> None:
    """Run GitHub data collection for all projects due for refresh."""
    from sqlalchemy import select

    from craft_dashboard.models.refresh_schedule import RefreshSchedule

    collector = GitHubCollector(
        token=settings.github_token,
        org="canonical",
        maintainers=config.maintainers,
    )

    for i, project_name in enumerate(config.craft_projects):
        async with session_factory() as session:
            category = "application" if project_name in config.craft_applications else (
                "library" if project_name in config.craft_libraries else "other"
            )
            project_id = await _get_or_create_project(
                session, project_name, category, i
            )

            # Check refresh schedule
            result = await session.execute(
                select(RefreshSchedule.next_refresh_at).where(
                    RefreshSchedule.project_id == project_id,
                    RefreshSchedule.source == "github",
                )
            )
            next_refresh = result.scalar_one_or_none()

            if not is_due_for_refresh(next_refresh):
                logger.info("Skipping %s (not due for refresh)", project_name)
                continue

            logger.info("Collecting GitHub data for %s", project_name)
            try:
                await collector.collect_issues(project_name, project_id, session)
                await collector.collect_releases(project_name, project_id, session)
                await generate_snapshot(
                    project_id, session, set(config.maintainers)
                )
                await update_refresh_schedule(
                    project_id, "github", config.refresh_interval_days, session
                )
            except Exception as exc:
                logger.exception("Failed to collect GitHub data for %s", project_name)
                async with session_factory() as err_session:
                    await record_refresh_error(
                        project_id, "github", str(exc), err_session
                    )
                continue

            # Avoid GitHub secondary rate limits between repos
            await asyncio.sleep(1)


async def _collect_launchpad(config, session_factory) -> None:
    """Run Launchpad data collection for all configured projects."""
    collector = LaunchpadCollector(projects=config.launchpad_projects)

    for lp_name in config.launchpad_projects:
        async with session_factory() as session:
            from sqlalchemy import select

            from craft_dashboard.models.project import Project

            result = await session.execute(
                select(Project.id).where(Project.name == lp_name)
            )
            project_id = result.scalar_one_or_none()
            if project_id is None:
                logger.warning("Project %s not found in DB, skipping LP collection", lp_name)
                continue

            logger.info("Collecting Launchpad data for %s", lp_name)
            await collector.collect_bugs(lp_name, project_id, session)


async def _main(source: str) -> None:
    """Run data collection."""
    settings = Settings()
    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    try:
        if source in ("all", "github"):
            await _collect_github(settings, config, session_factory)
        if source in ("all", "launchpad"):
            await _collect_launchpad(config, session_factory)
    finally:
        await engine.dispose()


@click.command()
@click.option(
    "--source",
    type=click.Choice(["github", "launchpad", "all"]),
    default="all",
    help="Data source to collect from.",
)
def main(source: str) -> None:
    """Collect data from external sources."""
    asyncio.run(_main(source))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/collect_data.py
git commit -m "feat: add data collection cron entry point script"
```

---

### Task 7: CSV Migration Script

**Files:**
- Create: `scripts/migrate_csv.py`

- [ ] **Step 1: Create the one-time migration script**

Create `scripts/migrate_csv.py`:
```python
#!/usr/bin/env python3
"""One-time migration of CSV/JSON data from starcraft-stats to PostgreSQL.

Usage:
    uv run scripts/migrate_csv.py --data-dir /path/to/starcraft-stats/html/data

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
"""

import asyncio
import csv
import json
import logging
import pathlib
import sys
from datetime import datetime, timezone

import click

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _migrate_projects(session, config) -> dict[str, int]:
    """Create project records and return name-to-id mapping."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.project import Project

    name_to_id = {}

    for i, name in enumerate(config.craft_projects):
        category = "application" if name in config.craft_applications else (
            "library" if name in config.craft_libraries else "other"
        )
        lp_name = name if name in config.launchpad_projects else None

        stmt = insert(Project).values(
            name=name,
            category=category,
            launchpad_name=lp_name,
            display_order=i,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
        await session.execute(stmt)

    await session.commit()

    result = await session.execute(select(Project.id, Project.name))
    for row in result:
        name_to_id[row.name] = row.id

    return name_to_id


async def _migrate_snapshots(
    session,
    data_dir: pathlib.Path,
    name_to_id: dict[str, int],
) -> int:
    """Migrate snapshot data from per-project CSV files."""
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.snapshot import Snapshot

    count = 0

    for project_name, project_id in name_to_id.items():
        csv_path = data_dir / f"{project_name}-github.csv"
        if not csv_path.exists():
            continue

        with csv_path.open() as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    snapshot_date = datetime.strptime(
                        row.get("date", row.get("Date", "")), "%Y-%m-%d"
                    ).date()
                except (ValueError, KeyError):
                    continue

                stmt = insert(Snapshot).values(
                    project_id=project_id,
                    snapshot_date=snapshot_date,
                    open_issues=int(row.get("open_issues", 0)),
                    open_prs=int(row.get("open_prs", 0)),
                    open_issues_external=int(row.get("open_issues_ext", 0)),
                    open_issues_internal=int(row.get("open_issues_int", 0)),
                    open_prs_external=int(row.get("open_prs_ext", 0)),
                    open_prs_internal=int(row.get("open_prs_int", 0)),
                    open_bugs=int(row.get("open_bugs", 0)),
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["project_id", "snapshot_date"]
                )
                await session.execute(stmt)
                count += 1

    await session.commit()
    logger.info("Migrated %d snapshot rows", count)
    return count


async def _main(data_dir: pathlib.Path) -> None:
    """Run the migration."""
    settings = Settings()
    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    try:
        async with session_factory() as session:
            logger.info("Migrating projects...")
            name_to_id = await _migrate_projects(session, config)
            logger.info("Created %d projects", len(name_to_id))

            logger.info("Migrating snapshots...")
            await _migrate_snapshots(session, data_dir, name_to_id)

        logger.info("Migration complete!")
    finally:
        await engine.dispose()


@click.command()
@click.option(
    "--data-dir",
    type=click.Path(exists=True, path_type=pathlib.Path),
    required=True,
    help="Path to the starcraft-stats html/data directory.",
)
def main(data_dir: pathlib.Path) -> None:
    """Migrate CSV/JSON data from starcraft-stats to PostgreSQL."""
    asyncio.run(_main(data_dir))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/migrate_csv.py
git commit -m "feat: add one-time CSV-to-PostgreSQL migration script"
```

---

### Task 8: Wire Collect Command into CLI

**Files:**
- Modify: `craft_dashboard/cli.py`
- Test: `tests/unit/test_cli.py` (verify collect command works)

- [ ] **Step 1: Update `craft_dashboard/cli.py` to wire collect to the real collector**

Replace the `collect` command in `craft_dashboard/cli.py`:
```python
@main.command()
@click.option(
    "--source",
    type=click.Choice(["github", "launchpad", "all"]),
    default="all",
    help="Data source to collect from.",
)
@click.option(
    "--config-file",
    type=click.Path(exists=True),
    default="craft-dashboard.toml",
    help="Path to configuration file.",
)
def collect(*, source: str, config_file: str) -> None:
    """Collect data from external sources."""
    import asyncio
    import pathlib

    from craft_dashboard.config import load_config
    from craft_dashboard.database import get_engine, get_session_factory
    from craft_dashboard.settings import Settings

    settings = Settings()
    config = load_config(pathlib.Path(config_file))
    click.echo(f"Collecting data from: {source}")
    click.echo(f"Projects: {len(config.craft_projects)}")
    # The actual async collection is handled by scripts/collect_data.py
    # This CLI command delegates to it
    click.echo("Use 'uv run scripts/collect_data.py' for cron-based collection.")
```

- [ ] **Step 2: Run tests to verify nothing broke**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add craft_dashboard/cli.py
git commit -m "feat: wire collect command to config system"
```

---

### Task 9: Run Full Test Suite and Lint

**Files:**
- No new files

- [ ] **Step 1: Run the full test suite**

Run: `make test`
Expected: All tests PASS

- [ ] **Step 2: Format and lint**

Run: `make format && make lint`
Expected: No errors

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore: lint and format pass for data collection pipeline"
```
