"""Tests for application settings."""

import pytest
from craft_dashboard.settings import Settings
from pydantic import ValidationError

_EXPECTED_GITHUB_TOKEN = "ghp_test123"
_EXPECTED_ADMIN_TOKEN = "admin-secret"
_EXPECTED_LOCAL_LLM_API_KEY = "my-bearer-token"


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
        monkeypatch.setenv("GITHUB_TOKEN", _EXPECTED_GITHUB_TOKEN)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test123")
        monkeypatch.setenv("ADMIN_TOKEN", _EXPECTED_ADMIN_TOKEN)
        monkeypatch.setenv("DEBUG", "true")

        settings = Settings()

        assert settings.database_url == "postgresql+asyncpg://db:5432/dashboard"
        assert settings.github_token == _EXPECTED_GITHUB_TOKEN
        assert settings.openrouter_api_key == "sk-or-test123"
        assert settings.admin_token == _EXPECTED_ADMIN_TOKEN
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
        monkeypatch.setenv("LOCAL_LLM_API_KEY", _EXPECTED_LOCAL_LLM_API_KEY)

        settings = Settings()

        assert settings.llm_backend == "local"
        assert settings.local_llm_url == "http://192.168.1.10:11434/v1"
        assert settings.local_llm_summary_model == "qwen2.5"
        assert settings.local_llm_api_key == _EXPECTED_LOCAL_LLM_API_KEY

    def test_llm_backend_rejects_invalid(self, monkeypatch) -> None:
        """Invalid LLM backends should be rejected."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        with pytest.raises(ValidationError):
            Settings(llm_backend="invalid_backend")

    def test_llm_backend_accepts_valid(self, monkeypatch) -> None:
        """Known LLM backends should validate."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        settings = Settings(llm_backend="local")

        assert settings.llm_backend == "local"

    def test_validate_required_secrets_returns_warnings_for_missing_tokens(
        self, monkeypatch
    ) -> None:
        """Missing admin and GitHub tokens return startup warnings."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        settings = Settings(_env_file=None)

        assert settings.validate_required_secrets() == [
            "ADMIN_TOKEN is not set. Admin endpoints will reject all requests.",
            "GITHUB_TOKEN is not set. Data collection will fail.",
        ]

    def test_validate_required_secrets_returns_empty_list_when_tokens_present(
        self, monkeypatch
    ) -> None:
        """Configured admin and GitHub tokens produce no warnings."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", _EXPECTED_ADMIN_TOKEN)
        monkeypatch.setenv("GITHUB_TOKEN", _EXPECTED_GITHUB_TOKEN)

        settings = Settings()

        assert settings.validate_required_secrets() == []


class TestValidateRequiredSecrets:
    def test_no_warnings_when_all_set(self, monkeypatch) -> None:
        """No warnings when all secrets are set."""
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        settings = Settings()
        assert settings.validate_required_secrets() == []

    def test_warnings_when_empty(self, monkeypatch) -> None:
        """Warnings for empty admin token and github token."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "")
        monkeypatch.setenv("GITHUB_TOKEN", "")
        settings = Settings()
        warnings = settings.validate_required_secrets()
        assert len(warnings) == 2
        assert any("ADMIN_TOKEN" in warning for warning in warnings)
        assert any("GITHUB_TOKEN" in warning for warning in warnings)
