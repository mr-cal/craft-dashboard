"""Configuration loading for craft-dashboard."""

import pathlib
import tomllib

from pydantic import BaseModel, Field


class DashboardConfig(BaseModel):
    """Configuration for the craft-dashboard application."""

    craft_applications: list[str] = Field(default_factory=list)
    craft_libraries: list[str] = Field(default_factory=list)
    craft_projects: list[str] = Field(default_factory=list)
    refresh_interval_days: int = 7
    launchpad_projects: list[str] = Field(default_factory=list)
    maintainers: list[str] = Field(default_factory=list)
    launchpad_maintainers: list[str] = Field(default_factory=list)
    bots: list[str] = Field(default_factory=list)
    hotfix_min_versions: dict[str, str] = Field(default_factory=dict)


def load_config(config_path: pathlib.Path) -> DashboardConfig:
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

    return DashboardConfig(**normalized)
