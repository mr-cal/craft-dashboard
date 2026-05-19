"""Tests for the configuration system."""

import pathlib
import textwrap

import pytest
from craft_dashboard.config import load_config


class TestDashboardConfig:
    """Tests for DashboardConfig."""

    def test_load_config_from_file(self, tmp_path: pathlib.Path) -> None:
        """Load a valid config file."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = ["snapcraft", "charmcraft"]
                craft-libraries = ["craft-cli"]
                craft-projects = ["snapcraft", "charmcraft", "craft-cli"]
                refresh-interval-days = 7
                launchpad-projects = ["snapcraft"]
                maintainers = ["mr-cal"]

                [hotfix-min-versions]
                snapcraft = "8.0"
            """)
        )

        config = load_config(config_file)

        assert config.craft_applications == ["snapcraft", "charmcraft"]
        assert config.craft_libraries == ["craft-cli"]
        assert config.craft_projects == ["snapcraft", "charmcraft", "craft-cli"]
        assert config.refresh_interval_days == 7
        assert config.launchpad_projects == ["snapcraft"]
        assert config.maintainers == ["mr-cal"]
        assert config.hotfix_min_versions == {"snapcraft": "8.0"}

    def test_load_config_default_refresh_interval(self, tmp_path: pathlib.Path) -> None:
        """Default refresh interval is 7 days."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = []
                craft-libraries = []
                craft-projects = []
                launchpad-projects = []
                maintainers = []
            """)
        )

        config = load_config(config_file)

        assert config.refresh_interval_days == 7

    def test_load_config_missing_file(self, tmp_path: pathlib.Path) -> None:
        """Raise FileNotFoundError for missing config file."""
        config_file = tmp_path / "nonexistent.toml"

        with pytest.raises(FileNotFoundError):
            load_config(config_file)
