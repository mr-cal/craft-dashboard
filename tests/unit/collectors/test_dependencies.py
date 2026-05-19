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
        collector = DependencyCollector(token="ghp_test", org="canonical")  # noqa: S106

        assert collector.org == "canonical"
