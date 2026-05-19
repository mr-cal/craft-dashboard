"""Tests for application settings."""

from craft_dashboard.settings import Settings


class TestSettings:
    """Tests for Settings."""

    def test_default_settings(self, monkeypatch) -> None:
        """Settings load with defaults when env vars are not set."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        settings = Settings()

        assert settings.database_url == "postgresql+asyncpg://localhost/test"
        assert settings.debug is False
        assert settings.host == "127.0.0.1"
        assert settings.port == 8000

    def test_settings_from_env(self, monkeypatch) -> None:
        """Settings load from environment variables."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://db:5432/dashboard")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test123")
        monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")
        monkeypatch.setenv("DEBUG", "true")

        settings = Settings()

        assert settings.database_url == "postgresql+asyncpg://db:5432/dashboard"
        assert settings.github_token == "ghp_test123"  # noqa: S105
        assert settings.openrouter_api_key == "sk-or-test123"
        assert settings.admin_token == "admin-secret"  # noqa: S105
        assert settings.debug is True

    def test_default_llm_backend(self, monkeypatch) -> None:
        """Default LLM backend is openrouter."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("LLM_BACKEND", "openrouter")
        monkeypatch.setenv("LOCAL_LLM_API_KEY", "")

        settings = Settings()

        assert settings.llm_backend == "openrouter"
        assert settings.local_llm_api_key == ""

    def test_local_llm_backend(self, monkeypatch) -> None:
        """Local LLM backend can be configured via env vars."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("LLM_BACKEND", "local")
        monkeypatch.setenv("LOCAL_LLM_URL", "http://192.168.1.10:11434/v1")
        monkeypatch.setenv("LOCAL_LLM_SUMMARY_MODEL", "qwen2.5")
        monkeypatch.setenv("LOCAL_LLM_API_KEY", "my-bearer-token")

        settings = Settings()

        assert settings.llm_backend == "local"
        assert settings.local_llm_url == "http://192.168.1.10:11434/v1"
        assert settings.local_llm_summary_model == "qwen2.5"
        assert settings.local_llm_api_key == "my-bearer-token"  # noqa: S105
