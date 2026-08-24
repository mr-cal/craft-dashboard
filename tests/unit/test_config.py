"""Tests for the configuration system."""

import pathlib
import textwrap
import tomllib

import pytest
from craft_dashboard.config import ForumConfig, load_config


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

    def test_load_config_malformed_toml(self, tmp_path: pathlib.Path) -> None:
        """Malformed TOML raises an error."""
        config_file = tmp_path / "bad.toml"
        config_file.write_text("this is not valid toml [[[")

        with pytest.raises(tomllib.TOMLDecodeError):
            load_config(config_file)

    def test_load_config_parses_filtered_issues(self, tmp_path: pathlib.Path) -> None:
        """[issues.filter] is parsed into filtered_issues as string IDs."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = ["snapcraft"]
                maintainers = ["alice"]

                [issues.filter]
                snapcraft = [4472, 100]
                charmcraft = [9999]
            """)
        )

        config = load_config(config_file)

        assert config.filtered_issues == {
            "snapcraft": ["4472", "100"],
            "charmcraft": ["9999"],
        }

    def test_load_config_no_filtered_issues_defaults_to_empty_dict(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Config without [issues.filter] has filtered_issues={}."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = ["snapcraft"]
                maintainers = ["alice"]
            """)
        )

        config = load_config(config_file)

        assert config.filtered_issues == {}

    def test_load_config_no_forums_defaults_to_empty_dict(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Config without any [forums.*] sections has forums={}."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = ["snapcraft"]
                maintainers = ["alice"]
            """)
        )

        config = load_config(config_file)

        assert config.forums == {}

    def test_load_config_parses_forums(self, tmp_path: pathlib.Path) -> None:
        """[forums.*] sections parse into ForumConfig with base_url/default_categories."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = ["snapcraft"]
                maintainers = ["alice"]

                [forums.snapcraft]
                base-url = "https://forum.snapcraft.io"
                default-categories = ["snapcraft", "questions"]
                display-name = "snapcraft forums"

                [forums.charmcraft]
                base-url = "https://discourse.charmhub.io"
            """)
        )

        config = load_config(config_file)

        assert set(config.forums) == {"snapcraft", "charmcraft"}
        assert config.forums["snapcraft"].base_url == "https://forum.snapcraft.io"
        assert config.forums["snapcraft"].default_categories == [
            "snapcraft",
            "questions",
        ]
        assert config.forums["snapcraft"].display_name == "snapcraft forums"
        # default_categories and display_name are optional.
        assert config.forums["charmcraft"].default_categories == []
        assert config.forums["charmcraft"].display_name is None

    def test_load_config_forum_has_no_categories_field(
        self, tmp_path: pathlib.Path
    ) -> None:
        """ForumConfig has no `categories` field: every forum is fully tracked."""
        assert "categories" not in ForumConfig.model_fields

    def test_load_config_forum_missing_base_url_raises(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A [forums.*] section without base-url fails validation."""
        config_file = tmp_path / "craft-dashboard.toml"
        config_file.write_text(
            textwrap.dedent("""\
                craft-applications = ["snapcraft"]
                maintainers = ["alice"]

                [forums.snapcraft]
                default-categories = ["snapcraft"]
            """)
        )

        with pytest.raises(Exception):  # noqa: B017, PT011 - pydantic ValidationError
            load_config(config_file)
