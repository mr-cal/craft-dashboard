"""Tests for application settings."""

from craft_dashboard.settings import Settings

_EXPECTED_GITHUB_TOKEN = "ghp_test123"
_EXPECTED_ADMIN_TOKEN = "admin-secret"


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

    def test_summary_and_evaluation_models_use_openrouter_settings(
        self, monkeypatch
    ) -> None:
        """Derived model properties always follow the OpenRouter settings."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")

        settings = Settings(
            openrouter_summary_model="google/gemini-2.5-flash",
            openrouter_evaluation_model="anthropic/claude-haiku-4.5",
        )

        assert settings.summary_model == "google/gemini-2.5-flash"
        assert settings.evaluation_model == "anthropic/claude-haiku-4.5"

    def test_ignores_removed_local_llm_environment_variables(self, monkeypatch) -> None:
        """Removed local LLM env vars no longer appear in server settings."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("LLM_BACKEND", "local")
        monkeypatch.setenv("LOCAL_LLM_URL", "http://192.168.1.10:11434/v1")
        monkeypatch.setenv("LOCAL_LLM_SUMMARY_MODEL", "qwen2.5")
        monkeypatch.setenv("LOCAL_LLM_EVALUATION_MODEL", "llama3.2")
        monkeypatch.setenv("LOCAL_LLM_API_KEY", "my-bearer-token")
        monkeypatch.setenv("LOCAL_LLM_CA_CERT", "/etc/ssl/local-llm/cert.pem")

        settings = Settings()

        assert not hasattr(settings, "llm_backend")
        assert not hasattr(settings, "local_llm_url")
        assert not hasattr(settings, "local_llm_summary_model")
        assert not hasattr(settings, "local_llm_evaluation_model")
        assert not hasattr(settings, "local_llm_api_key")
        assert not hasattr(settings, "local_llm_ca_cert")

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
            "EVAL_API_TOKEN is not set. Eval API endpoints will reject all requests.",
        ]

    def test_validate_required_secrets_returns_empty_list_when_tokens_present(
        self, monkeypatch
    ) -> None:
        """Configured admin and GitHub tokens produce no warnings."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", _EXPECTED_ADMIN_TOKEN)
        monkeypatch.setenv("GITHUB_TOKEN", _EXPECTED_GITHUB_TOKEN)
        monkeypatch.setenv("EVAL_API_TOKEN", "eval-token")

        settings = Settings()

        assert settings.validate_required_secrets() == []


class TestValidateRequiredSecrets:
    def test_no_warnings_when_all_set(self, monkeypatch) -> None:
        """No warnings when all secrets are set."""
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        monkeypatch.setenv("EVAL_API_TOKEN", "eval-token")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        settings = Settings()
        assert settings.validate_required_secrets() == []

    def test_warnings_when_empty(self, monkeypatch) -> None:
        """Warnings for empty admin token and github token."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("ADMIN_TOKEN", "")
        monkeypatch.setenv("GITHUB_TOKEN", "")
        settings = Settings(_env_file=None, eval_api_token="")
        warnings = settings.validate_required_secrets()
        assert len(warnings) == 3
        assert any("ADMIN_TOKEN" in warning for warning in warnings)
        assert any("GITHUB_TOKEN" in warning for warning in warnings)
        assert any("EVAL_API_TOKEN" in warning for warning in warnings)
