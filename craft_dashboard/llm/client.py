"""HTTP clients for LLM API calls."""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from craft_dashboard.llm.exceptions import LLMQuotaError, LLMUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

    from craft_dashboard.settings import Settings

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
HTTP_TOO_MANY_REQUESTS = 429
HTTP_PAYMENT_REQUIRED = 402


def _make_before_attempt(max_attempts: int) -> Callable[[RetryCallState], None]:
    """Build a tenacity ``before`` hook that reports attempt progress.

    Reads ``retry_callback`` off the instance the retried method is bound to
    (``retry_state.args[0]`` is ``self``) so callers such as the eval CLI can
    surface "(N/M attempts)" in a progress display without the client needing
    to know anything about the UI.
    """

    def _before(retry_state: RetryCallState) -> None:
        instance = retry_state.args[0] if retry_state.args else None
        callback = getattr(instance, "retry_callback", None)
        if callback is not None:
            callback(retry_state.attempt_number, max_attempts)

    return _before


def _make_before_sleep_log(max_attempts: int) -> Callable[[RetryCallState], None]:
    """Build a tenacity ``before_sleep`` hook that logs each retry.

    Unlike ``before`` (which tenacity only calls once, ahead of the very
    first attempt), ``before_sleep`` fires right before every retry, once
    the previous attempt's failure is known -- this is the layer that was
    previously completely silent, even though a retried/discarded attempt
    can still incur real provider cost (e.g. a dropped connection after the
    model already started generating).
    """

    def _before_sleep(retry_state: RetryCallState) -> None:
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        if exception is None:
            return
        logger.warning(
            "HTTP retry (attempt %d/%d): %s",
            retry_state.attempt_number,
            max_attempts,
            exception,
        )

    return _before_sleep


@dataclass
class LLMResponse:
    """Parsed response from an LLM API."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str
    # Actual billed USD cost for this call, as reported by OpenRouter itself
    # (``usage.cost``). None for backends that don't report it (e.g. the
    # local LLM server), in which case callers fall back to the static
    # per-token pricing table.
    cost_usd: float | None = None
    # Native tool_calls from the response, if the model invoked any (see
    # plans/36-deep-evaluation-design.md section 1). None when the model
    # returned plain content instead. Each entry is the raw OpenAI-format
    # dict: {"id": ..., "type": "function", "function": {"name": ..., "arguments": "<json str>"}}.
    tool_calls: list[dict[str, Any]] | None = None
    # The model's reasoning/thinking trace, if the provider returned one.
    # OpenRouter normalizes this across providers into ``message.reasoning``
    # and includes it by default whenever a supporting model decides to
    # output it -- previously this was silently dropped, which is why
    # bake-off transcripts had no visibility into *why* a model reached a
    # given score even for models that were actually reasoning internally.
    reasoning: str | None = None

    @classmethod
    def from_api_response(cls, data: dict) -> LLMResponse:
        """Parse an LLM API response dict."""
        # Some providers (and thinking models under certain conditions, e.g.
        # hitting max_tokens or triggering content filters) return a null
        # message content instead of an empty string. Coerce to "" so
        # downstream parsing can treat it as an unparsable response instead
        # of crashing on None.
        message = data["choices"][0]["message"]
        content = message["content"] or ""
        usage = data.get("usage", {})
        return cls(
            content=content,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            model=data.get("model", ""),
            cost_usd=usage.get("cost"),
            tool_calls=message.get("tool_calls"),
            reasoning=message.get("reasoning") or None,
        )


@runtime_checkable
class LLMClient(Protocol):
    """Shared protocol for LLM completion clients."""

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Return a completion response for the supplied messages."""


def _is_retriable(exc: BaseException) -> bool:
    """Return True for transient errors that should trigger a retry.

    Retries on 429 (rate limited) and network/timeout/protocol errors.
    ``httpx.TransportError`` covers ``TimeoutException``, ``NetworkError``,
    and ``ProtocolError`` (e.g. ``RemoteProtocolError`` raised when the
    server drops the connection without sending a response).
    Does NOT retry on 402 (quota exhausted) or other 4xx errors.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == HTTP_TOO_MANY_REQUESTS
    return isinstance(exc, httpx.TransportError)


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
        # Optional hook invoked as (attempt_number, max_attempts) before each
        # retry attempt of complete(); lets callers surface retry progress.
        self.retry_callback: Callable[[int, int], None] | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        """Return a persistent HTTP client, creating one if needed."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=600.0)
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    @retry(
        retry=retry_if_exception(_is_retriable),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        stop=stop_after_attempt(3),
        before=_make_before_attempt(3),
        before_sleep=_make_before_sleep_log(3),
        reraise=True,
    )
    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a completion request to OpenRouter."""
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Explicitly request reasoning tokens (OpenRouter's unified
            # ``reasoning`` field across providers) so that models which
            # support a visible thinking trace surface it in
            # ``message.reasoning`` instead of silently reasoning
            # internally with nothing for us to inspect. Harmless no-op for
            # models that don't support reasoning.
            "reasoning": {"enabled": True},
        }
        if response_format:
            payload["response_format"] = response_format
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

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
        self.ca_cert = str(pathlib.Path(ca_cert).expanduser()) if ca_cert else ca_cert
        self._http: httpx.AsyncClient | None = None
        # Optional hook invoked as (attempt_number, max_attempts) before each
        # retry attempt of complete(); lets callers surface retry progress.
        self.retry_callback: Callable[[int, int], None] | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        """Return a persistent HTTP client, creating one if needed."""
        if self._http is None or self._http.is_closed:
            verify: bool | str = self.ca_cert if self.ca_cert else True
            self._http = httpx.AsyncClient(timeout=600.0, verify=verify)
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    @retry(
        retry=retry_if_exception(
            lambda exc: isinstance(exc, (httpx.TransportError, LLMUnavailableError))
        ),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(5),
        before=_make_before_attempt(5),
        before_sleep=_make_before_sleep_log(5),
        reraise=True,
    )
    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        response_format: dict | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
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
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = await self.http.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        if response.status_code >= 500:  # noqa: PLR2004
            raise LLMUnavailableError(
                f"LLM server returned {response.status_code} — "
                "the backend may be down or overloaded"
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
