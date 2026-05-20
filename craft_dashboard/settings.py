"""Application settings loaded from environment variables."""

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
    llm_backend: str = "openrouter"

    # Local LLM settings (any OpenAI-compatible server)
    local_llm_url: str = "http://localhost:11434/v1"
    local_llm_api_key: str = ""
    local_llm_summary_model: str = "llama3.2"
    local_llm_evaluation_model: str = "llama3.2"

    # OpenRouter model settings
    openrouter_summary_model: str = "google/gemini-2.5-flash-lite"
    openrouter_evaluation_model: str = "anthropic/claude-haiku-4.5"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
