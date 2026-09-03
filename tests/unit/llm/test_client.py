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

    @pytest.mark.asyncio
    async def test_complete_requests_reasoning_tokens(self) -> None:
        """Every request opts into OpenRouter's unified reasoning field so
        supporting models surface their thinking trace instead of it being
        silently discarded.
        """
        mock_response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi"}}]},
            request=httpx.Request("POST", "http://x"),
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            client = OpenRouterClient(api_key="test")

            await client.complete(
                model="test/model", messages=[{"role": "user", "content": "hi"}]
            )

        _args, kwargs = mock_post.call_args
        assert kwargs["json"]["reasoning"] == {"enabled": True}


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

    def test_init_with_ca_cert(self, tmp_path) -> None:
        """LocalLLMClient stores a CA cert path for self-signed TLS."""
        cert_file = tmp_path / "cert.pem"
        cert_file.write_text("dummy-cert")
        client = LocalLLMClient(ca_cert=str(cert_file))

        assert client.ca_cert == str(cert_file)

    def test_init_with_missing_ca_cert_raises(self, tmp_path) -> None:
        """LocalLLMClient raises FileNotFoundError with helpful message when CA cert does not exist."""
        nonexistent = tmp_path / "nonexistent_cert.pem"
        with pytest.raises(
            FileNotFoundError, match="LLM CA certificate file not found"
        ):
            LocalLLMClient(ca_cert=str(nonexistent))

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
        assert response.cost_usd is None

    def test_from_api_response_captures_cost(self) -> None:
        """OpenRouter's own usage.cost is the authoritative billed amount --
        capture it so callers can prefer it over the static pricing table.
        """
        api_data = {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {
                "total_tokens": 154,
                "prompt_tokens": 17,
                "completion_tokens": 137,
                "cost": 0.00013921,
            },
        }

        response = LLMResponse.from_api_response(api_data)

        assert response.cost_usd == 0.00013921

    def test_from_api_response_missing_usage(self) -> None:
        """Handle response with missing usage data."""
        api_data = {
            "choices": [{"message": {"content": "hello"}}],
        }

        response = LLMResponse.from_api_response(api_data)

        assert response.content == "hello"
        assert response.total_tokens == 0
        assert response.model == ""

    def test_from_api_response_null_content(self) -> None:
        """Coerce a null message content (e.g. content filter, max_tokens
        cutoff with no text) to an empty string instead of leaving it None.
        """
        api_data = {
            "choices": [{"message": {"content": None}}],
            "usage": {"total_tokens": 10, "prompt_tokens": 10, "completion_tokens": 0},
        }

        response = LLMResponse.from_api_response(api_data)

        assert response.content == ""
        assert response.total_tokens == 10

    def test_from_api_response_captures_reasoning(self) -> None:
        """OpenRouter surfaces a model's thinking trace in
        ``message.reasoning`` by default when the model supports it -- this
        must be captured, not silently dropped, or bake-off/debug transcripts
        have no visibility into why a model reached a given answer.
        """
        api_data = {
            "choices": [
                {
                    "message": {
                        "content": '{"summary": "done"}',
                        "reasoning": "Step 1: read the issue. Step 2: ...",
                    }
                }
            ],
        }

        response = LLMResponse.from_api_response(api_data)

        assert response.reasoning == "Step 1: read the issue. Step 2: ..."

    def test_from_api_response_defaults_reasoning_to_none(self) -> None:
        """Models/providers that don't return reasoning leave it unset."""
        api_data = {"choices": [{"message": {"content": "hello"}}]}

        response = LLMResponse.from_api_response(api_data)

        assert response.reasoning is None

    def test_from_api_response_captures_finish_reason_and_reasoning_tokens(
        self,
    ) -> None:
        """finish_reason and reasoning-token usage must be captured so
        callers can tell a deliberate final answer apart from one the model
        was cut off mid-thought while producing (e.g. finish_reason ==
        "length"), which otherwise looks identical to a normal completion.
        """
        api_data = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "{}", "reasoning": "still thinking..."},
                }
            ],
            "usage": {
                "completion_tokens_details": {"reasoning_tokens": 4096},
            },
        }

        response = LLMResponse.from_api_response(api_data)

        assert response.finish_reason == "length"
        assert response.reasoning_tokens == 4096

    def test_from_api_response_defaults_finish_reason_and_reasoning_tokens_to_none(
        self,
    ) -> None:
        """Providers that omit these fields leave them unset rather than
        crashing.
        """
        api_data = {"choices": [{"message": {"content": "hello"}}]}

        response = LLMResponse.from_api_response(api_data)

        assert response.finish_reason is None
        assert response.reasoning_tokens is None


class TestOpenRouterClientRetryLogging:
    """Tests for the client-level retry (429/transport errors)."""

    @pytest.mark.asyncio
    async def test_retry_after_429_is_logged(self, monkeypatch, caplog) -> None:
        """This retry layer previously logged nothing at all, silently
        discarding the tokens/cost of the failed attempt. Confirm it now
        surfaces a warning so retries are visible in run output.
        """
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        responses = iter(
            [
                httpx.Response(429, request=request),
                httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "ok"}}],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    },
                    request=request,
                ),
            ]
        )

        async def _fake_post(*_args: object, **_kwargs: object) -> httpx.Response:
            return next(responses)

        client = OpenRouterClient(api_key="test")
        caplog.set_level("WARNING")
        with (
            patch.object(client, "_http", None),
            patch("httpx.AsyncClient.post", new=_fake_post),
        ):
            result = await client.complete(
                model="test/model", messages=[{"role": "user", "content": "hi"}]
            )

        assert result.content == "ok"
        assert "HTTP retry" in caplog.text
