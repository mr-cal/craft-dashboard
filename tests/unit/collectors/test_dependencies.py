"""Tests for the dependency collector."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
import urllib3
from github import GithubException, UnknownObjectException

from craft_dashboard.collectors.dependencies import (
    DependencyCollector,
    get_latest_for_branch,
    parse_requirements_line,
    parse_uv_lock,
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


class TestParseRequirementsLineEdgeCases:
    def test_dash_prefix_ignored(self) -> None:
        """Lines starting with - are ignored (pip options)."""
        assert parse_requirements_line("-e git+https://...") is None
        assert parse_requirements_line("--index-url https://...") is None

    def test_complex_version_spec(self) -> None:
        """Complex version specs with multiple constraints."""
        name, spec = parse_requirements_line("foo>=1.0,<2.0")

        assert name == "foo"
        assert spec == ">=1.0,<2.0"

    def test_whitespace_handling(self) -> None:
        """Leading/trailing whitespace is stripped."""
        name, spec = parse_requirements_line("  requests>=2.0  ")

        assert name == "requests"
        assert spec == ">=2.0"


class TestParseUvLock:
    """Tests for parse_uv_lock."""

    _SAMPLE = """
version = 1

[[package]]
name = "craft-application"
version = "3.1.4"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "craft_cli"
version = "2.9.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "requests"
version = "2.32.3"
source = { registry = "https://pypi.org/simple" }
"""

    def test_basic_parsing(self) -> None:
        """parse_uv_lock extracts name→version pairs."""
        packages = parse_uv_lock(self._SAMPLE)

        assert packages["craft-application"] == "3.1.4"
        assert packages["requests"] == "2.32.3"

    def test_name_normalisation(self) -> None:
        """Underscores in package names are normalised to dashes."""
        packages = parse_uv_lock(self._SAMPLE)

        # craft_cli → craft-cli
        assert packages["craft-cli"] == "2.9.0"

    def test_package_with_dots_in_name(self) -> None:
        """Dots in package names are normalised to dashes."""
        content = '[[package]]\nname = "my.package"\nversion = "1.0.0"\n'
        result = parse_uv_lock(content)

        assert "my-package" in result

    def test_multiple_packages(self) -> None:
        """Multiple packages are all extracted."""
        content = (
            '[[package]]\nname = "foo"\nversion = "1.0"\n\n'
            '[[package]]\nname = "bar"\nversion = "2.0"\n'
        )
        result = parse_uv_lock(content)

        assert result["foo"] == "1.0"
        assert result["bar"] == "2.0"

    def test_empty_lock(self) -> None:
        """An empty (but valid) lock file returns an empty dict."""
        packages = parse_uv_lock("version = 1\n")

        assert packages == {}


class TestGetLatestForBranch:
    """Tests for get_latest_for_branch."""

    _VERSIONS = ["3.1.4", "3.1.5", "3.2.0", "3.3.2", "4.0.0"]

    def test_main_returns_global_latest(self) -> None:
        """On main, the globally latest stable version is returned."""
        latest = get_latest_for_branch("main", self._VERSIONS, "3.1.4")

        assert latest == "4.0.0"

    def test_hotfix_branch_returns_series_latest(self) -> None:
        """On a hotfix branch, the latest in the same minor series is returned."""
        latest = get_latest_for_branch("hotfix/3.1", self._VERSIONS, "3.1.4")

        assert latest == "3.1.5"

    def test_hotfix_branch_different_series(self) -> None:
        """Hotfix branch for 3.2 returns latest 3.2.x."""
        latest = get_latest_for_branch("hotfix/3.2", self._VERSIONS, "3.2.0")

        assert latest == "3.2.0"

    def test_hotfix_no_series_match_returns_global(self) -> None:
        """Hotfix branch with no matching series falls back to global latest."""
        latest = get_latest_for_branch("hotfix/2.0", ["3.0.0", "4.0.0"], "2.0.0")

        assert latest == "4.0.0"

    def test_single_version(self) -> None:
        """Single version is returned for main."""
        latest = get_latest_for_branch("main", ["1.0.0"], "1.0.0")

        assert latest == "1.0.0"

    def test_empty_versions_returns_installed(self) -> None:
        """If no versions are available, the installed version is returned."""
        latest = get_latest_for_branch("main", [], "3.1.4")

        assert latest == "3.1.4"

    def test_filters_prereleases(self) -> None:
        """Pre-release versions are excluded from consideration."""
        versions = ["3.1.4", "3.1.5a1", "3.2.0b2"]
        latest = get_latest_for_branch("main", versions, "3.1.4")

        assert latest == "3.1.4"


class TestIsOutdatedLogic:
    """Tests for is_outdated computation via get_latest_for_branch."""

    def test_outdated_when_behind(self) -> None:
        """is_outdated is True when installed < latest in series."""
        from packaging.version import Version  # noqa: PLC0415

        installed = "3.1.4"
        latest = get_latest_for_branch("hotfix/3.1", ["3.1.4", "3.1.5"], installed)
        is_outdated = Version(latest) > Version(installed)

        assert is_outdated is True

    def test_not_outdated_when_current(self) -> None:
        """is_outdated is False when installed == latest."""
        from packaging.version import Version  # noqa: PLC0415

        installed = "3.1.5"
        latest = get_latest_for_branch("hotfix/3.1", ["3.1.4", "3.1.5"], installed)
        is_outdated = Version(latest) > Version(installed)

        assert is_outdated is False


class TestDependencyCollector:
    """Tests for DependencyCollector."""

    def test_init(self) -> None:
        """DependencyCollector initializes with a token and org."""
        collector = DependencyCollector(token="ghp_test", org="canonical")  # noqa: S106

        assert collector.org == "canonical"

    def test_init_configures_timeout_and_retry(self) -> None:
        """DependencyCollector configures PyGithub timeout and retries."""
        with patch("craft_dashboard.collectors.dependencies.Github") as mock_github:
            DependencyCollector(token="ghp_test", org="canonical")  # noqa: S106

        mock_github.assert_called_once_with(auth=ANY, timeout=30, retry=ANY)
        retry = mock_github.call_args.kwargs["retry"]
        assert isinstance(retry, urllib3.Retry)
        assert retry.total == 3
        assert retry.backoff_factor == 1
        assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}

    def test_init_with_craft_libraries(self) -> None:
        """DependencyCollector stores craft_libraries."""
        libs = ["craft-application", "craft-cli"]
        collector = DependencyCollector(  # noqa: S106
            token="ghp_test", org="canonical", craft_libraries=libs
        )

        assert collector.craft_libraries == libs

    def test_init_default_craft_libraries(self) -> None:
        """craft_libraries defaults to an empty list."""
        collector = DependencyCollector(token="ghp_test", org="canonical")  # noqa: S106

        assert collector.craft_libraries == []


class TestCollectDependenciesExceptionHandling:
    """Tests for collect_dependencies() exception handling."""

    @staticmethod
    def _fake_insert(_table) -> MagicMock:
        stmt = MagicMock()
        stmt.excluded = MagicMock()
        stmt.values.return_value = stmt
        stmt.on_conflict_do_update.return_value = stmt
        return stmt

    @staticmethod
    def _make_contents(text: str) -> MagicMock:
        contents = MagicMock()
        contents.decoded_content = text.encode()
        return contents

    @staticmethod
    def _make_repo(get_contents_side_effect) -> MagicMock:
        repo = MagicMock()
        repo.get_contents.side_effect = get_contents_side_effect
        return repo

    @staticmethod
    def _make_session() -> AsyncMock:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=None)
        session.commit = AsyncMock()
        return session

    @staticmethod
    def _pyproject_contents() -> MagicMock:
        return TestCollectDependenciesExceptionHandling._make_contents(
            '[project]\ndependencies = ["requests>=2.0"]\n'
        )

    @pytest.mark.parametrize(
        "exc",
        [
            GithubException(500, {"message": "boom"}),
            UnknownObjectException(404, {"message": "missing"}),
        ],
    )
    async def test_collect_dependencies_catches_missing_uv_lock(
        self, mocker, exc
    ) -> None:
        collector = DependencyCollector(token="ghp_test", org="canonical")  # noqa: S106

        def get_contents(path: str, ref: str):
            if path == "uv.lock":
                raise exc
            if path == "pyproject.toml":
                return self._pyproject_contents()
            raise AssertionError(path)

        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = self._make_repo(get_contents)
        session = self._make_session()

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._fake_insert,
        )

        count = await collector.collect_dependencies("repo", 1, ["main"], session)

        assert count == 1
        session.commit.assert_awaited_once()

    async def test_collect_dependencies_propagates_non_github_exception_fetching_uv_lock(
        self, mocker
    ) -> None:
        collector = DependencyCollector(token="ghp_test", org="canonical")  # noqa: S106

        def get_contents(path: str, ref: str):
            if path == "uv.lock":
                raise RuntimeError("boom")
            if path == "pyproject.toml":
                return self._pyproject_contents()
            raise AssertionError(path)

        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = self._make_repo(get_contents)
        session = self._make_session()

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._fake_insert,
        )

        with pytest.raises(RuntimeError, match="boom"):
            await collector.collect_dependencies("repo", 1, ["main"], session)

    @pytest.mark.parametrize(
        "exc",
        [
            GithubException(500, {"message": "boom"}),
            UnknownObjectException(404, {"message": "missing"}),
        ],
    )
    async def test_collect_dependencies_catches_missing_pyproject(
        self, mocker, exc
    ) -> None:
        collector = DependencyCollector(token="ghp_test", org="canonical")  # noqa: S106

        def get_contents(path: str, ref: str):
            if path == "uv.lock":
                raise UnknownObjectException(404, {"message": "missing"})
            if path == "pyproject.toml":
                raise exc
            raise AssertionError(path)

        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = self._make_repo(get_contents)
        session = self._make_session()

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._fake_insert,
        )

        count = await collector.collect_dependencies("repo", 1, ["main"], session)

        assert count == 0
        session.execute.assert_not_awaited()
        session.commit.assert_awaited_once()

    async def test_collect_dependencies_propagates_non_github_exception_fetching_pyproject(
        self, mocker
    ) -> None:
        collector = DependencyCollector(token="ghp_test", org="canonical")  # noqa: S106

        def get_contents(path: str, ref: str):
            if path == "uv.lock":
                raise UnknownObjectException(404, {"message": "missing"})
            if path == "pyproject.toml":
                raise RuntimeError("boom")
            raise AssertionError(path)

        collector.gh = MagicMock()
        collector.gh.get_repo.return_value = self._make_repo(get_contents)
        session = self._make_session()

        mocker.patch(
            "sqlalchemy.dialects.postgresql.insert",
            side_effect=self._fake_insert,
        )

        with pytest.raises(RuntimeError, match="boom"):
            await collector.collect_dependencies("repo", 1, ["main"], session)


class TestGetPyPIVersionsAsync:
    async def test_pypi_timeout_returns_empty(self) -> None:
        """Network timeout returns empty list."""
        import httpx
        from unittest.mock import AsyncMock

        from craft_dashboard.collectors.dependencies import (
            _PYPI_CACHE,
            get_pypi_versions,
        )

        _PYPI_CACHE.pop("test-pkg-timeout", None)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = await get_pypi_versions("test-pkg-timeout")

        assert result == []

    async def test_pypi_filters_prerelease_versions(self) -> None:
        """Pre-release versions are filtered from PyPI releases."""
        from unittest.mock import AsyncMock, MagicMock

        from craft_dashboard.collectors.dependencies import (
            _PYPI_CACHE,
            get_pypi_versions,
        )

        _PYPI_CACHE.pop("test-pkg-versions", None)

        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "releases": {
                "1.0.0": {},
                "1.0.1": {},
                "1.1.0rc1": {},
            }
        }

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_cls.return_value = mock_client

            result = await get_pypi_versions("test-pkg-versions")

        assert result == ["1.0.0", "1.0.1"]
