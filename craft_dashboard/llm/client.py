"""HTTP clients for LLM API calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from craft_dashboard.llm.exceptions import LLMQuotaError

if TYPE_CHECKING:
    from craft_dashboard.settings import Settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
HTTP_TOO_MANY_REQUESTS = 429
HTTP_PAYMENT_REQUIRED = 402


@dataclass
class LLMResponse:
    """Parsed response from an LLM API."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str

    @classmethod
    def from_api_response(cls, data: dict) -> LLMResponse:
        """Parse an LLM API response dict."""
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return cls(
            content=content,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            model=data.get("model", ""),
        )


@runtime_checkable
class LLMClient(Protocol):
    """Shared protocol for LLM completion clients."""

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """Return a completion response for the supplied messages."""


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
    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """Send a completion request to OpenRouter."""
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
            raise LLMQuotaError(
                "OpenRouter daily quota exhausted. "
                "Evaluation will resume tomorrow after reset."
            )

        response.raise_for_status()

        data = response.json()
        result = LLMResponse.from_api_response(data)
        logger.info(
            "LLM call: requested_model=%s, actual_model=%s, tokens=%d (prompt=%d, completion=%d)",
            model,
            result.model or model,
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

    def __init__(
        self, base_url: str = LOCAL_LLM_BASE_URL, api_key: str = "", ca_cert: str = ""
    ) -> None:
        """Initialize the local LLM client.

        Args:
            base_url: Base URL for the OpenAI-compatible API endpoint.
                      Default: http://localhost:11434/v1
            api_key: Optional bearer token for servers that require authentication.
            ca_cert: Path to a PEM CA certificate for verifying the server's TLS
                     certificate. Required when base_url uses https:// with a
                     self-signed cert. Leave empty to use the system CA bundle.

        """
        self.base_url = base_url
        self.api_key = api_key
        self.ca_cert = ca_cert
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        """Return a persistent HTTP client, creating one if needed."""
        if self._http is None or self._http.is_closed:
            verify: bool | str = self.ca_cert if self.ca_cert else True
            self._http = httpx.AsyncClient(timeout=120.0, verify=verify)
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
    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """Send a completion request to the local LLM server."""
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
        result = LLMResponse.from_api_response(data)
        logger.info(
            "Local LLM call: requested_model=%s, actual_model=%s, tokens=%d (prompt=%d, completion=%d)",
            model,
            result.model or model,
            result.total_tokens,
            result.prompt_tokens,
            result.completion_tokens,
        )
        return result


def create_llm_client(settings: Settings) -> LLMClient:
    """Create the server-side LLM client."""
    return OpenRouterClient(api_key=settings.openrouter_api_key)
