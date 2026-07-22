"""Tests for the EmbeddingClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from craft_dashboard.llm.embeddings import EmbeddingClient


def _make_response(
    embeddings: list[list[float]], model: str = "nomic-embed-text"
) -> httpx.Response:
    data = [{"embedding": vec, "index": i} for i, vec in enumerate(embeddings)]
    return httpx.Response(
        200,
        json={
            "data": data,
            "model": model,
            "usage": {"prompt_tokens": 10, "total_tokens": 10},
        },
        request=httpx.Request("POST", "http://x"),
    )


@pytest.mark.asyncio
async def test_embed_returns_vector():
    response = _make_response([[0.1, 0.2, 0.3]])
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response):
        client = EmbeddingClient(base_url="http://localhost:11434/v1")
        try:
            result = await client.embed("test text")
        finally:
            await client.close()
    assert result == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_batch_preserves_order():
    # Return results in reverse order to verify sorting by index
    reversed_data = [
        {"embedding": [0.3, 0.4], "index": 1},
        {"embedding": [0.1, 0.2], "index": 0},
    ]
    response = httpx.Response(
        200,
        json={"data": reversed_data, "model": "nomic-embed-text", "usage": {}},
        request=httpx.Request("POST", "http://x"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response):
        client = EmbeddingClient(base_url="http://localhost:11434/v1")
        try:
            results = await client.embed_batch(["text a", "text b"])
        finally:
            await client.close()
    assert results[0] == [0.1, 0.2]
    assert results[1] == [0.3, 0.4]


@pytest.mark.asyncio
async def test_embed_sends_api_key():
    response = _make_response([[0.1]])
    with patch(
        "httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response
    ) as mock_post:
        client = EmbeddingClient(base_url="http://localhost:11434/v1", api_key="secret")
        try:
            await client.embed("test")
        finally:
            await client.close()
    _args, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_embed_raises_on_http_error():
    response = httpx.Response(500, request=httpx.Request("POST", "http://x"))
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response):
        client = EmbeddingClient(base_url="http://localhost:11434/v1")
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await client.embed("test")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_embed_batch_sends_dimensions_when_requested():
    response = _make_response([[0.1, 0.2, 0.3]])
    with patch(
        "httpx.AsyncClient.post", new_callable=AsyncMock, return_value=response
    ) as mock_post:
        client = EmbeddingClient(base_url="http://localhost:11434/v1")
        try:
            await client.embed_batch(["test text"], dimensions=1024)
        finally:
            await client.close()
    _args, kwargs = mock_post.call_args
    assert kwargs["json"]["dimensions"] == 1024
