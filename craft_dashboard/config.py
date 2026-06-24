"""Configuration loading for craft-dashboard."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

SCHEDULE_DAY_MIN = 0
SCHEDULE_DAY_MAX = 6


class DashboardConfig(BaseModel):
    """Configuration for the craft-dashboard application."""

    craft_applications: list[str] = Field(default_factory=list)
    craft_libraries: list[str] = Field(default_factory=list)
    craft_projects: list[str] = Field(default_factory=list)
    refresh_interval_days: int = 7
    schedule_days: list[int] = Field(default_factory=list)
    launchpad_projects: list[str] = Field(default_factory=list)
    maintainers: list[str] = Field(default_factory=list)
    launchpad_maintainers: list[str] = Field(default_factory=list)
    bots: list[str] = Field(default_factory=list)
    hotfix_min_versions: dict[str, str] = Field(default_factory=dict)
    filtered_issues: dict[str, list[str]] = Field(default_factory=dict)

    @classmethod
    def validate(cls, value: object) -> DashboardConfig:
        """Validate cross-field dashboard configuration requirements."""
        config = cls.model_validate(value)
        configured_projects = (
            config.craft_applications
            + config.craft_libraries
            + config.craft_projects
            + config.launchpad_projects
        )
        if not configured_projects:
            raise ValueError("At least one project must be configured")
        if not config.maintainers:
            raise ValueError("Maintainers list must not be empty")
        invalid_days = [
            day
            for day in config.schedule_days
            if day < SCHEDULE_DAY_MIN or day > SCHEDULE_DAY_MAX
        ]
        if invalid_days:
            raise ValueError("schedule days must be between 0 and 6")
        return config


def load_config(config_path: Path) -> DashboardConfig:
    """Load configuration from a TOML file.

    Args:
        config_path: Path to the TOML configuration file.

    Returns:
        A validated DashboardConfig instance.

    Raises:
        FileNotFoundError: If the config file does not exist.

    """
    if not config_path.exists():
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)

    with config_path.open("rb") as f:
        raw = tomllib.load(f)

    # Convert TOML kebab-case keys to Python snake_case
    normalized = {key.replace("-", "_"): value for key, value in raw.items()}

    # Handle nested sections
    if "hotfix_min_versions" in normalized:
        normalized["hotfix_min_versions"] = dict(normalized["hotfix_min_versions"])
    if "schedule" in normalized and isinstance(normalized["schedule"], dict):
        schedule = normalized.pop("schedule")
        normalized["schedule_days"] = schedule.get("days", [])
    if "issues" in normalized:
        issues_section = normalized.pop("issues")
        if isinstance(issues_section, dict) and "filter" in issues_section:
            normalized["filtered_issues"] = {
                project: [str(n) for n in ids]
                for project, ids in issues_section["filter"].items()
            }

    return DashboardConfig(**normalized)
