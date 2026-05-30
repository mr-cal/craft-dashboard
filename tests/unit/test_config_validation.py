"""Tests for centralized settings and config validation."""

import pathlib

import pytest
from craft_dashboard.config import DashboardConfig
from craft_dashboard.settings import Settings


class TestSettingsValidation:
    """Tests for Settings validation helpers."""

    def test_openrouter_backend_requires_api_key(self, monkeypatch) -> None:
        """OpenRouter settings require an API key when validated."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        settings = Settings(openrouter_api_key="")

        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            Settings.validate_config(settings)

    def test_local_backend_uses_local_models(self, monkeypatch) -> None:
        """Derived model properties follow the selected backend."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        settings = Settings(
            llm_backend="local",
            local_llm_summary_model="qwen2.5",
            local_llm_evaluation_model="llama3.2",
        )

        assert settings.summary_model == "qwen2.5"
        assert settings.evaluation_model == "llama3.2"

    def test_validate_config_rejects_missing_config_file(self, monkeypatch) -> None:
        """Validation rejects config paths that do not exist."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        settings = Settings(
            config_file="missing-config.toml", openrouter_api_key="sk-test"
        )

        with pytest.raises(ValueError, match="config_file"):
            Settings.validate_config(settings)

    def test_validate_config_accepts_existing_config_file(self, monkeypatch) -> None:
        """Validation accepts the repository default config file."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        settings = Settings(
            config_file="craft-dashboard.toml", openrouter_api_key="sk-test"
        )

        Settings.validate_config(settings)

        assert pathlib.Path(settings.config_file).name == "craft-dashboard.toml"


class TestDashboardConfigValidation:
    """Tests for DashboardConfig validation."""

    def test_validate_requires_at_least_one_project(self) -> None:
        """Validation rejects empty project configuration."""
        config = DashboardConfig(maintainers=["mr-cal"])

        with pytest.raises(ValueError, match="At least one project"):
            DashboardConfig.validate(config)

    def test_validate_requires_non_empty_maintainers(self) -> None:
        """Validation rejects configs without maintainers."""
        config = DashboardConfig(craft_projects=["snapcraft"])

        with pytest.raises(ValueError, match="Maintainers"):
            DashboardConfig.validate(config)

    def test_validate_rejects_invalid_schedule_days(self) -> None:
        """Validation rejects schedule days outside 0-6."""
        config = DashboardConfig(
            craft_projects=["snapcraft"],
            maintainers=["mr-cal"],
            schedule_days=[0, 7],
        )

        with pytest.raises(ValueError, match="schedule"):
            DashboardConfig.validate(config)

    def test_validate_accepts_valid_config(self) -> None:
        """Validation accepts a config with projects, maintainers, and schedule."""
        config = DashboardConfig(
            craft_projects=["snapcraft"],
            maintainers=["mr-cal"],
            schedule_days=[1, 3, 5],
        )

        assert DashboardConfig.validate(config) == config
