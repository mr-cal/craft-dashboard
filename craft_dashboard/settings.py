"""Application settings loaded from environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, loaded from environment variables or .env file."""

    database_url: str = "postgresql+asyncpg://localhost/craft_dashboard"
    github_token: str = ""
    openrouter_api_key: str = ""
    admin_token: str = ""
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    config_file: str = "craft-dashboard.toml"
    log_level: str = "INFO"

    # LLM backend: "openrouter" (production) or "local" (local LLM server)
    llm_backend: Literal["openrouter", "local"] = "openrouter"

    # Local LLM settings (any OpenAI-compatible server)
    local_llm_url: str = "http://localhost:11434/v1"
    local_llm_api_key: str = ""
    local_llm_summary_model: str = "llama3.2"
    local_llm_evaluation_model: str = "llama3.2"
    # Path to a PEM CA cert for verifying the local LLM server's TLS certificate.
    # Required when LOCAL_LLM_URL uses https:// with a self-signed cert.
    local_llm_ca_cert: str = ""

    # OpenRouter model settings
    openrouter_summary_model: str = "google/gemini-2.5-flash-lite"
    openrouter_evaluation_model: str = "anthropic/claude-haiku-4.5"

    # Database pool settings
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # How many days before re-fetching an issue from GitHub
    refresh_age_days: int = 7

    @property
    def summary_model(self) -> str:
        """Return the summary model for the selected LLM backend."""
        if self.llm_backend == "local":
            return self.local_llm_summary_model
        return self.openrouter_summary_model

    @property
    def evaluation_model(self) -> str:
        """Return the evaluation model for the selected LLM backend."""
        if self.llm_backend == "local":
            return self.local_llm_evaluation_model
        return self.openrouter_evaluation_model

    @property
    def config_path(self) -> Path:
        """Return the validated path to the dashboard config file."""
        config_path = Path(self.config_file)
        if not config_path.exists():
            raise ValueError(f"config_file does not exist: {self.config_file}")
        return config_path

    @classmethod
    def validate_config(cls, settings: Settings) -> None:
        """Validate derived configuration requirements for the active backend."""
        if settings.llm_backend == "openrouter" and not settings.openrouter_api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required when LLM_BACKEND=openrouter"
            )
        if settings.llm_backend == "local" and not settings.local_llm_url:
            raise ValueError("LOCAL_LLM_URL is required when LLM_BACKEND=local")
        _ = settings.config_path

    def validate_required_secrets(self) -> list[str]:
        """Return a list of warning messages for missing secrets."""
        warnings: list[str] = []
        if not self.admin_token:
            warnings.append(
                "ADMIN_TOKEN is not set. Admin endpoints will reject all requests."
            )
        if not self.github_token:
            warnings.append("GITHUB_TOKEN is not set. Data collection will fail.")
        return warnings

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
