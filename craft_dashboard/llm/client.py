"""OpenRouter HTTP client for LLM API calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from craft_dashboard.settings import Settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
HTTP_TOO_MANY_REQUESTS = 429
HTTP_PAYMENT_REQUIRED = 402


class QuotaExhaustedError(Exception):
    """Raised when the OpenRouter daily quota is exhausted (HTTP 402)."""


@dataclass
class OpenRouterResponse:
    """Parsed response from the OpenRouter API."""

    content: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int

    @classmethod
    def from_api_response(cls, data: dict) -> OpenRouterResponse:
        """Parse an OpenRouter API response dict.

        Args:
            data: Raw JSON response from the API.

        Returns:
            A parsed OpenRouterResponse.

        """
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return cls(
            content=content,
            total_tokens=usage.get("total_tokens", 0),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )


def _is_retriable(exc: BaseException) -> bool:
    """Return True for transient errors that should trigger a retry.

    Retries on 429 (rate limited) and network/timeout errors.
    Does NOT retry on 402 (quota exhausted) or other 4xx errors.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == HTTP_TOO_MANY_REQUESTS
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


class OpenRouterClient:
    """HTTP client for the OpenRouter API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = OPENROUTER_BASE_URL,
    ) -> None:
        """Initialize the OpenRouter client.

        Args:
            api_key: OpenRouter API key.
            base_url: Base URL for the API.

        """
        self.api_key = api_key
        self.base_url = base_url
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        """Return a persistent HTTP client, creating one if needed."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=60.0)
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    @retry(
        retry=retry_if_exception(_is_retriable),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
    ) -> OpenRouterResponse:
        """Send a chat completion request to OpenRouter.

        Automatically retries on 429 (rate limited) or network errors with
        exponential backoff (up to 3 attempts). Raises QuotaExhaustedError
        immediately on 402 (daily quota exhausted) without retrying.

        Args:
            model: Model identifier (e.g., 'google/gemini-flash-1.5').
            messages: List of message dicts with 'role' and 'content'.
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Maximum tokens in the response.
            response_format: Optional response format spec (e.g., JSON mode).

        Returns:
            Parsed OpenRouterResponse.

        Raises:
            QuotaExhaustedError: If the daily quota is exhausted (HTTP 402).
            httpx.HTTPStatusError: If the API returns any other error status.

        """
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        response = await self.http.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/mr-cal/craft-dashboard",
                "X-Title": "craft-dashboard",
            },
            json=payload,
        )

        if response.status_code == HTTP_PAYMENT_REQUIRED:
            raise QuotaExhaustedError(
                "OpenRouter daily quota exhausted. "
                "Evaluation will resume tomorrow after reset."
            )

        response.raise_for_status()

        data = response.json()
        result = OpenRouterResponse.from_api_response(data)
        logger.info(
            "LLM call: model=%s, tokens=%d (prompt=%d, completion=%d)",
            model,
            result.total_tokens,
            result.prompt_tokens,
            result.completion_tokens,
        )
        return result


LOCAL_LLM_BASE_URL = "http://localhost:11434/v1"


class LocalLLMClient:
    """HTTP client for a locally-hosted LLM (any OpenAI-compatible server).

    Retries on timeout/network errors only.
    """

    def __init__(self, base_url: str = LOCAL_LLM_BASE_URL, api_key: str = "") -> None:
        """Initialize the local LLM client.

        Args:
            base_url: Base URL for the OpenAI-compatible API endpoint.
                      Default: http://localhost:11434/v1
            api_key: Optional bearer token for servers that require authentication.

        """
        self.base_url = base_url
        self.api_key = api_key
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        """Return a persistent HTTP client, creating one if needed."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=120.0)
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    @retry(
        retry=retry_if_exception(
            lambda exc: isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
        ),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
    ) -> OpenRouterResponse:
        """Send a chat completion request to the local LLM server.

        Args:
            model: Local model name (e.g., 'llama3.2', 'mistral', 'qwen2.5').
            messages: List of message dicts with 'role' and 'content'.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.
            response_format: Optional response format (JSON mode if supported).

        Returns:
            Parsed OpenRouterResponse (same structure as OpenRouter).

        Raises:
            httpx.HTTPStatusError: If the server returns an error status.

        """
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self.http.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

        data = response.json()
        result = OpenRouterResponse.from_api_response(data)
        logger.info(
            "Local LLM call: model=%s, tokens=%d (prompt=%d, completion=%d)",
            model,
            result.total_tokens,
            result.prompt_tokens,
            result.completion_tokens,
        )
        return result


def create_llm_client(settings: Settings) -> OpenRouterClient | LocalLLMClient:
    """Create the appropriate LLM client based on settings.llm_backend.

    Args:
        settings: Application Settings instance.

    Returns:
        OpenRouterClient for 'openrouter' backend, LocalLLMClient for 'local'.

    Raises:
        ValueError: If llm_backend is not recognized.

    """
    if settings.llm_backend == "local":
        return LocalLLMClient(
            base_url=settings.local_llm_url, api_key=settings.local_llm_api_key
        )
    if settings.llm_backend == "openrouter":
        return OpenRouterClient(api_key=settings.openrouter_api_key)
    raise ValueError(
        f"Unknown llm_backend: {settings.llm_backend!r}. Use 'openrouter' or 'local'."
    )
