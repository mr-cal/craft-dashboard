"""Client for computing text embeddings via an OpenAI-compatible API."""

from __future__ import annotations

import logging
import pathlib

import httpx

logger = logging.getLogger(__name__)

_EMBEDDING_DIMENSION = 768  # nomic-embed-text-v1.5


class EmbeddingClient:
    """Compute embeddings using an OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434/v1",
        model: str = "nomic-embed-text",
        api_key: str = "",
        ca_cert: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.ca_cert = str(pathlib.Path(ca_cert).expanduser()) if ca_cert else ""
        self._http: httpx.AsyncClient | None = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            verify: bool | str = self.ca_cert if self.ca_cert else True
            self._http = httpx.AsyncClient(timeout=60.0, verify=verify)
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    async def embed(self, text: str) -> list[float]:
        """Compute an embedding for a single text string."""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings for multiple texts in one API call."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = await self._client.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json={"model": self.model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()
        # Sort by index to guarantee order matches input regardless of API response order
        sorted_data = sorted(data["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in sorted_data]
