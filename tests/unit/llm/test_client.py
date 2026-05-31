"""Tests for the OpenRouter client."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from craft_dashboard.llm.client import (
    LLMResponse,
    LocalLLMClient,
    OpenRouterClient,
    create_llm_client,
)
from craft_dashboard.llm.exceptions import LLMQuotaError
from craft_dashboard.settings import Settings


class TestOpenRouterClient:
    """Tests for OpenRouterClient."""

    def test_init(self) -> None:
        """Client initializes with API key."""
        client = OpenRouterClient(api_key="sk-or-test123")

        assert client.api_key == "sk-or-test123"

    def test_init_with_custom_base_url(self) -> None:
        """Client accepts custom base URL."""
        client = OpenRouterClient(
            api_key="sk-or-test",
            base_url="https://custom.api/v1",
        )

        assert client.base_url == "https://custom.api/v1"


class TestQuotaError:
    """Tests for quota error detection."""

    @pytest.mark.asyncio
    async def test_raises_quota_error_on_402(self) -> None:
        """HTTP 402 response raises LLMQuotaError, not HTTPStatusError."""
        mock_response = httpx.Response(402, request=httpx.Request("POST", "http://x"))

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            client = OpenRouterClient(api_key="test")

            with pytest.raises(LLMQuotaError):
                await client.complete(
                    model="test/model", messages=[{"role": "user", "content": "hi"}]
                )


class TestPersistentHttpClient:
    def test_http_property_creates_client(self) -> None:
        """The http property creates a client on first access."""
        client = OpenRouterClient(api_key="test")
        assert client._http is None
        http = client.http
        assert http is not None
        assert not http.is_closed

    async def test_close_closes_client(self) -> None:
        """close() closes the underlying HTTP client."""
        client = OpenRouterClient(api_key="test")
        _ = client.http
        await client.close()
        assert client._http.is_closed


class TestLocalLLMClient:
    """Tests for LocalLLMClient."""

    def test_init_default_url(self) -> None:
        """LocalLLMClient uses the configured base URL."""
        client = LocalLLMClient()

        assert "localhost:11434" in client.base_url

    def test_init_custom_url(self) -> None:
        """LocalLLMClient accepts a custom base URL."""
        client = LocalLLMClient(base_url="http://192.168.1.5:11434/v1")

        assert client.base_url == "http://192.168.1.5:11434/v1"

    def test_init_with_api_key(self) -> None:
        """LocalLLMClient stores an optional API key."""
        client = LocalLLMClient(api_key="my-bearer-token")

        assert client.api_key == "my-bearer-token"

    def test_init_default_no_api_key(self) -> None:
        """LocalLLMClient defaults to no API key."""
        client = LocalLLMClient()

        assert client.api_key == ""

    def test_init_with_ca_cert(self) -> None:
        """LocalLLMClient stores a CA cert path for self-signed TLS."""
        client = LocalLLMClient(ca_cert="/etc/ssl/local-llm/cert.pem")

        assert client.ca_cert == "/etc/ssl/local-llm/cert.pem"

    def test_init_default_no_ca_cert(self) -> None:
        """LocalLLMClient defaults to no CA cert (uses system CA bundle)."""
        client = LocalLLMClient()

        assert client.ca_cert == ""


class TestCreateLLMClient:
    """Tests for the create_llm_client factory."""

    def test_returns_openrouter_client(self, monkeypatch) -> None:
        """create_llm_client always returns OpenRouterClient."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        client = create_llm_client(Settings())

        assert isinstance(client, OpenRouterClient)

    def test_ignores_removed_local_backend_environment_variables(
        self, monkeypatch
    ) -> None:
        """Server-side client creation ignores removed local backend config."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("LLM_BACKEND", "local")
        monkeypatch.setenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LOCAL_LLM_API_KEY", "my-secret-token")
        monkeypatch.setenv("LOCAL_LLM_CA_CERT", "/etc/ssl/local-llm/cert.pem")

        client = create_llm_client(Settings())

        assert isinstance(client, OpenRouterClient)
        assert client.api_key == "sk-or-test"


class TestLLMResponse:
    """Tests for LLMResponse."""

    def test_from_api_response(self) -> None:
        """Parse a typical OpenRouter API response."""
        api_data = {
            "choices": [
                {
                    "message": {
                        "content": '{"summary": "Test summary"}',
                    }
                }
            ],
            "usage": {
                "total_tokens": 150,
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
        }

        response = LLMResponse.from_api_response(api_data)

        assert response.content == '{"summary": "Test summary"}'
        assert response.total_tokens == 150
        assert response.prompt_tokens == 100
        assert response.completion_tokens == 50
        assert response.model == ""

    def test_from_api_response_missing_usage(self) -> None:
        """Handle response with missing usage data."""
        api_data = {
            "choices": [{"message": {"content": "hello"}}],
        }

        response = LLMResponse.from_api_response(api_data)

        assert response.content == "hello"
        assert response.total_tokens == 0
        assert response.model == ""
