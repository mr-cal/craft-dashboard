"""Tests for the unified LLM client protocol."""

from typing import cast

import pytest
from craft_dashboard.llm.client import (
    LLMClient,
    LLMResponse,
    LocalLLMClient,
    OpenRouterClient,
)


class TestLLMResponse:
    """Tests for the shared LLM response type."""

    def test_from_api_response_includes_model(self) -> None:
        """Parsed API responses include the actual model name used."""
        api_data = {
            "model": "google/gemini-2.5-flash-lite",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {
                "total_tokens": 10,
                "prompt_tokens": 6,
                "completion_tokens": 4,
            },
        }

        response = LLMResponse.from_api_response(api_data)

        assert response.content == "hello"
        assert response.model == "google/gemini-2.5-flash-lite"
        assert response.total_tokens == 10
        assert response.prompt_tokens == 6
        assert response.completion_tokens == 4


class TestLLMClientProtocol:
    """Tests for the runtime-checkable LLM client protocol."""

    @pytest.mark.parametrize(
        "client",
        [
            OpenRouterClient(api_key="test"),
            LocalLLMClient(),
        ],
    )
    def test_clients_satisfy_protocol(self, client: object) -> None:
        """Both LLM client implementations satisfy the shared protocol."""
        typed_client = cast(LLMClient, client)

        assert isinstance(client, LLMClient)
        assert callable(typed_client.complete)
