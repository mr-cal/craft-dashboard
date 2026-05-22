"""Tests for the dependency collector."""

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

