"""Client for computing text embeddings via an OpenAI-compatible API."""

from __future__ import annotations

import logging
import pathlib

import httpx

logger = logging.getLogger(__name__)

HTTP_BAD_REQUEST = 400
_MIN_TRUNCATE_LEN = 1000


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
        if ca_cert:
            expanded = pathlib.Path(ca_cert).expanduser()
            if not expanded.is_file():
                raise FileNotFoundError(
                    f"Embedding CA certificate file not found: '{ca_cert}' (resolved to '{expanded}'). "
                    "Please check your CA certificate configuration."
                )
            self.ca_cert = str(expanded)
        else:
            self.ca_cert = ""
        self._http: httpx.AsyncClient | None = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            verify: bool | str = self.ca_cert if self.ca_cert else True
            try:
                self._http = httpx.AsyncClient(timeout=60.0, verify=verify)
            except FileNotFoundError as exc:
                raise FileNotFoundError(
                    f"Embedding CA certificate file not found: '{verify}'. "
                    "Please check your CA certificate configuration."
                ) from exc
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    async def embed(self, text: str, *, dimensions: int | None = None) -> list[float]:
        """Compute an embedding for a single text string."""
        results = await self.embed_batch([text], dimensions=dimensions)
        return results[0]

    async def embed_batch(
        self, texts: list[str], *, dimensions: int | None = None
    ) -> list[list[float]]:
        """Compute embeddings for multiple texts in one API call."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, object] = {"model": self.model, "input": texts}
        if dimensions is not None:
            payload["dimensions"] = dimensions

        response = await self._client.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json=payload,
        )
        if response.is_error:
            error_body = response.text
            if (
                response.status_code == HTTP_BAD_REQUEST
                and "token" in error_body.lower()
                and any(len(t) > _MIN_TRUNCATE_LEN for t in texts)
            ):
                logger.warning(
                    "Embedding input exceeded model token limit (%s); truncating input texts and retrying.",
                    error_body,
                )
                truncated_texts = [t[: len(t) // 2] for t in texts]
                return await self.embed_batch(truncated_texts, dimensions=dimensions)
            raise httpx.HTTPStatusError(
                f"Client error '{response.status_code} {response.reason_phrase}' for url '{response.url}': {error_body}",
                request=response.request,
                response=response,
            )
        data = response.json()
        # Sort by index to guarantee order matches input regardless of API response order
        sorted_data = sorted(data["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in sorted_data]
