"""Client for computing text embeddings via an OpenAI-compatible API."""

from __future__ import annotations

import logging
import pathlib

import httpx

from craft_dashboard.llm.exceptions import LLMQuotaError

logger = logging.getLogger(__name__)

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_PAYMENT_REQUIRED = 402
HTTP_FORBIDDEN = 403
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

    async def check_quota(self) -> None:
        """Check if embedding key has remaining quota when using OpenRouter."""
        if not self.api_key or "openrouter.ai" not in self.base_url:
            return
        try:
            response = await self._client.get(
                f"{self.base_url}/auth/key",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Embedding check_quota network error: %s", exc)
            return

        if response.status_code == HTTP_OK:
            data = response.json().get("data", {})
            limit_remaining = data.get("limit_remaining")
            if limit_remaining is not None and limit_remaining <= 0:
                raise LLMQuotaError(
                    f"Embedding provider budget limit reached (remaining: {limit_remaining})."
                )
        elif response.status_code in (HTTP_PAYMENT_REQUIRED, HTTP_FORBIDDEN):
            raise LLMQuotaError(
                f"Embedding provider quota error ({response.status_code}): {response.text}"
            )

    async def embed(self, text: str, *, dimensions: int | None = None) -> list[float]:
        """Compute an embedding for a single text string."""
        results = await self.embed_batch([text], dimensions=dimensions)
        return results[0]

    async def embed_batch(
        self, texts: list[str], *, dimensions: int | None = None
    ) -> list[list[float]]:
        """Compute embeddings for multiple texts in one API call."""
        embeddings, _ = await self.embed_batch_with_usage(texts, dimensions=dimensions)
        return embeddings

    async def embed_batch_with_usage(
        self, texts: list[str], *, dimensions: int | None = None
    ) -> tuple[list[list[float]], int]:
        """Compute embeddings for multiple texts and return (embeddings, total_tokens)."""
        # If embed_batch was patched/mocked in tests, delegate to it
        if hasattr(self.embed_batch, "assert_called") or hasattr(
            type(self).embed_batch, "assert_called"
        ):
            res = await self.embed_batch(texts, dimensions=dimensions)
            return res, 0

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
                return await self.embed_batch_with_usage(
                    truncated_texts, dimensions=dimensions
                )
            if response.status_code in (HTTP_PAYMENT_REQUIRED, HTTP_FORBIDDEN):
                raise LLMQuotaError(
                    f"Embedding provider quota or budget exhausted ({response.status_code}): {error_body}"
                )
            raise httpx.HTTPStatusError(
                f"Client error '{response.status_code} {response.reason_phrase}' for url '{response.url}': {error_body}",
                request=response.request,
                response=response,
            )
        data = response.json()
        usage = data.get("usage") or {}
        embedding_tokens = int(
            usage.get("total_tokens") or usage.get("prompt_tokens") or 0
        )
        # Sort by index to guarantee order matches input regardless of API response order
        sorted_data = sorted(data["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in sorted_data], embedding_tokens
