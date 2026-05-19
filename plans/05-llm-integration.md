# Plan 5: LLM Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the LLM evaluation pipeline that scores and summarizes issues/PRs using OpenRouter. The pipeline detects changed issues, evaluates them with appropriate models (smaller for summarization, larger for scoring), and stores results in PostgreSQL. Designed to run as a daily cron job after data collection.

**Architecture:** A simple Python module that calls the OpenRouter API via httpx. No agent frameworks — just prompt templates, an HTTP client, and database interaction. Token optimization via content hashing (skip unchanged issues) and model selection (cheap model for summaries, better model for scores).

**Tech Stack:** httpx, OpenRouter API, PostgreSQL (via SQLAlchemy)

> **Existing code to read before implementing:** `starcraft_stats/issues.py` (issue field structure and what data is available for prompts), `starcraft_stats/models/issues.py` (field names).

**Depends on:** Plans 1, 2, and 3

---

### Task 1: OpenRouter HTTP Client

**Files:**
- Create: `craft_dashboard/llm/__init__.py`
- Create: `craft_dashboard/llm/client.py`
- Test: `tests/unit/llm/__init__.py`
- Test: `tests/unit/llm/test_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/llm/__init__.py`:
```python
```

Create `tests/unit/llm/test_client.py`:
```python
"""Tests for the OpenRouter client."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from craft_dashboard.llm.client import (
    LocalLLMClient,
    OpenRouterClient,
    OpenRouterResponse,
    QuotaExhaustedError,
    create_llm_client,
)


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


class TestQuotaExhaustedError:
    """Tests for QuotaExhaustedError detection."""

    @pytest.mark.asyncio
    async def test_raises_quota_error_on_402(self) -> None:
        """HTTP 402 response raises QuotaExhaustedError, not HTTPStatusError."""
        import httpx
        from unittest.mock import AsyncMock, patch

        mock_response = httpx.Response(402, request=httpx.Request("POST", "http://x"))

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            client = OpenRouterClient(api_key="test")

            with pytest.raises(QuotaExhaustedError):
                await client.chat(model="test/model", messages=[{"role": "user", "content": "hi"}])


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


class TestCreateLLMClient:
    """Tests for the create_llm_client factory."""

    def test_openrouter_backend(self, monkeypatch) -> None:
        """create_llm_client returns OpenRouterClient for openrouter backend."""
        from craft_dashboard.settings import Settings
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("LLM_BACKEND", "openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        client = create_llm_client(Settings())

        assert isinstance(client, OpenRouterClient)

    def test_local_backend(self, monkeypatch) -> None:
        """create_llm_client returns LocalLLMClient for local backend."""
        from craft_dashboard.settings import Settings
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("LLM_BACKEND", "local")

        client = create_llm_client(Settings())

        assert isinstance(client, LocalLLMClient)


class TestOpenRouterResponse:
    """Tests for OpenRouterResponse."""

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

        response = OpenRouterResponse.from_api_response(api_data)

        assert response.content == '{"summary": "Test summary"}'
        assert response.total_tokens == 150
        assert response.prompt_tokens == 100
        assert response.completion_tokens == 50

    def test_from_api_response_missing_usage(self) -> None:
        """Handle response with missing usage data."""
        api_data = {
            "choices": [{"message": {"content": "hello"}}],
        }

        response = OpenRouterResponse.from_api_response(api_data)

        assert response.content == "hello"
        assert response.total_tokens == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/llm/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/llm/__init__.py`:
```python
"""LLM integration for issue/PR evaluation."""
```

Create `craft_dashboard/llm/client.py`:
```python
"""OpenRouter HTTP client for LLM API calls."""

import logging
from dataclasses import dataclass

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


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
    def from_api_response(cls, data: dict) -> "OpenRouterResponse":
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
        return exc.response.status_code == 429
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
            temperature: Sampling temperature (0.0–2.0).
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

        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/mr-cal/craft-dashboard",
                    "X-Title": "craft-dashboard",
                },
                json=payload,
                timeout=60.0,
            )

            if response.status_code == 402:
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

    No API key or quota management required. Retries on timeout/network errors only.
    """

    def __init__(self, base_url: str = LOCAL_LLM_BASE_URL) -> None:
        """Initialize the local LLM client.

        Args:
            base_url: Base URL for the OpenAI-compatible API endpoint.
                      Default: http://localhost:11434/v1
        """
        self.base_url = base_url

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

        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"{self.base_url}/chat/completions",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=120.0,  # Local models can be slow
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


def create_llm_client(settings) -> "OpenRouterClient | LocalLLMClient":
    """Factory: create the appropriate LLM client based on settings.llm_backend.

    Args:
        settings: Application Settings instance.

    Returns:
        OpenRouterClient for 'openrouter' backend, LocalLLMClient for 'local'.

    Raises:
        ValueError: If llm_backend is not recognized.
    """
    if settings.llm_backend == "local":
        return LocalLLMClient(base_url=settings.local_llm_url)
    if settings.llm_backend == "openrouter":
        return OpenRouterClient(api_key=settings.openrouter_api_key)
    raise ValueError(f"Unknown llm_backend: {settings.llm_backend!r}. Use 'openrouter' or 'local'.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/llm/test_client.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/llm/ tests/unit/llm/
git commit -m "feat: add OpenRouter HTTP client"
```

---

### Task 2: Prompt Templates

**Files:**
- Create: `craft_dashboard/llm/prompts.py`
- Test: `tests/unit/llm/test_prompts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/llm/test_prompts.py`:
```python
"""Tests for LLM prompt templates."""

from craft_dashboard.llm.prompts import (
    build_evaluation_prompt,
    build_summary_prompt,
)


class TestBuildSummaryPrompt:
    """Tests for build_summary_prompt."""

    def test_returns_messages_list(self) -> None:
        """Returns a list of message dicts."""
        messages = build_summary_prompt(
            title="Bug: crash on startup",
            body="The app crashes when I open it.",
            issue_type="issue",
            labels=["bug"],
        )

        assert isinstance(messages, list)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_includes_issue_content(self) -> None:
        """The user message includes the issue title and body."""
        messages = build_summary_prompt(
            title="Feature request: dark mode",
            body="Please add dark mode support.",
            issue_type="issue",
            labels=["enhancement"],
        )

        user_msg = messages[1]["content"]
        assert "Feature request: dark mode" in user_msg
        assert "dark mode support" in user_msg


class TestBuildEvaluationPrompt:
    """Tests for build_evaluation_prompt."""

    def test_returns_messages_list(self) -> None:
        """Returns a list of message dicts."""
        messages = build_evaluation_prompt(
            title="Old PR",
            body="This PR was opened a year ago.",
            issue_type="pull_request",
            labels=[],
            age_days=365,
            last_activity_days=180,
            author="some-user",
            is_maintainer=False,
            comment_count=2,
        )

        assert isinstance(messages, list)
        assert len(messages) == 2

    def test_pr_specific_scores(self) -> None:
        """PR evaluation prompt mentions readiness score."""
        messages = build_evaluation_prompt(
            title="Add feature X",
            body="Implements feature X.",
            issue_type="pull_request",
            labels=[],
            age_days=30,
            last_activity_days=5,
            author="dev",
            is_maintainer=True,
            comment_count=10,
        )

        user_msg = messages[1]["content"]
        assert "readiness" in user_msg.lower()

    def test_issue_specific_scores(self) -> None:
        """Issue evaluation prompt mentions support_request score."""
        messages = build_evaluation_prompt(
            title="How do I install?",
            body="I can't figure out how to install this.",
            issue_type="issue",
            labels=[],
            age_days=10,
            last_activity_days=10,
            author="new-user",
            is_maintainer=False,
            comment_count=0,
        )

        user_msg = messages[1]["content"]
        assert "support_request" in user_msg.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/llm/test_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/llm/prompts.py`:
```python
"""Prompt templates for LLM evaluation of issues and PRs."""

_SUMMARY_SYSTEM = (
    "You are a concise technical writer. Summarize the following GitHub "
    "issue or pull request in 1-2 sentences. Focus on what the issue is "
    "about and its current status. Do not include markdown formatting."
)

_EVALUATION_SYSTEM = """\
You are an expert open-source project maintainer. Evaluate the following \
GitHub issue or pull request and provide scores and a suggested action.

Respond with valid JSON matching this schema:
{
  "scores": {
    "staleness": <0-100, how stale/inactive is this>,
    "relevance": <0-100, how relevant is this to the project>,
    "duplicateness": <0-100, how likely is this a duplicate>,
    "complexity": <0-100, how complex is this>,
    <additional scores based on type>
  },
  "suggested_action": "<one of: close_stale, close_duplicate, close_not_a_bug, \
close_outdated, needs_triage, needs_review, needs_rebase, keep_open>",
  "suggested_action_reason": "<brief explanation for the suggested action>"
}

Score guidelines:
- staleness: 0 = very active, 100 = completely dead (no activity in months)
- relevance: 0 = not relevant, 100 = critically important
- duplicateness: 0 = unique, 100 = clearly a duplicate
- complexity: 0 = trivial, 100 = extremely complex
"""

_ISSUE_EXTRA_SCORES = """
For issues, also include:
- "support_request": <0-100, how likely this is a support/help request rather than a bug or feature>
"""

_PR_EXTRA_SCORES = """
For pull requests, also include:
- "readiness": <0-100, how ready is this PR for review and merge>
"""


def build_summary_prompt(
    *,
    title: str,
    body: str | None,
    issue_type: str,
    labels: list[str],
) -> list[dict[str, str]]:
    """Build a prompt for summarizing an issue or PR.

    Args:
        title: Issue/PR title.
        body: Issue/PR body text.
        issue_type: 'issue' or 'pull_request'.
        labels: List of label names.

    Returns:
        List of message dicts for the LLM API.
    """
    type_label = "Pull Request" if issue_type == "pull_request" else "Issue"
    label_str = ", ".join(labels) if labels else "none"

    user_content = (
        f"Type: {type_label}\n"
        f"Title: {title}\n"
        f"Labels: {label_str}\n"
        f"Body:\n{body or '(no body)'}"
    )

    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_evaluation_prompt(
    *,
    title: str,
    body: str | None,
    issue_type: str,
    labels: list[str],
    age_days: int,
    last_activity_days: int,
    author: str,
    is_maintainer: bool,
    comment_count: int,
) -> list[dict[str, str]]:
    """Build a prompt for evaluating and scoring an issue or PR.

    Args:
        title: Issue/PR title.
        body: Issue/PR body text.
        issue_type: 'issue' or 'pull_request'.
        labels: List of label names.
        age_days: Days since creation.
        last_activity_days: Days since last update.
        author: Author username.
        is_maintainer: Whether the author is a project maintainer.
        comment_count: Number of comments.

    Returns:
        List of message dicts for the LLM API.
    """
    type_label = "Pull Request" if issue_type == "pull_request" else "Issue"
    label_str = ", ".join(labels) if labels else "none"
    extra_scores = _PR_EXTRA_SCORES if issue_type == "pull_request" else _ISSUE_EXTRA_SCORES

    system_content = _EVALUATION_SYSTEM + extra_scores

    user_content = (
        f"Type: {type_label}\n"
        f"Title: {title}\n"
        f"Labels: {label_str}\n"
        f"Author: {author} ({'maintainer' if is_maintainer else 'external contributor'})\n"
        f"Age: {age_days} days\n"
        f"Last activity: {last_activity_days} days ago\n"
        f"Comment count: {comment_count}\n"
        f"Body:\n{(body or '(no body)')[:3000]}"
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/llm/test_prompts.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/llm/prompts.py tests/unit/llm/test_prompts.py
git commit -m "feat: add LLM prompt templates for summarization and evaluation"
```

---

### Task 3: Issue Evaluator

**Files:**
- Create: `craft_dashboard/llm/evaluator.py`
- Test: `tests/unit/llm/test_evaluator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/llm/test_evaluator.py`:
```python
"""Tests for the issue evaluator."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from craft_dashboard.llm.evaluator import (
    IssueEvaluator,
    _needs_reevaluation,
    _parse_evaluation_response,
)


class TestNeedsReevaluation:
    """Tests for _needs_reevaluation."""

    def test_no_existing_evaluation(self) -> None:
        """Issues with no evaluation always need evaluation."""
        assert _needs_reevaluation(
            existing_hash=None, current_hash="abc123"
        ) is True

    def test_hash_changed(self) -> None:
        """Issues with changed content need re-evaluation."""
        assert _needs_reevaluation(
            existing_hash="old_hash", current_hash="new_hash"
        ) is True

    def test_hash_unchanged(self) -> None:
        """Issues with unchanged content don't need re-evaluation."""
        assert _needs_reevaluation(
            existing_hash="same_hash", current_hash="same_hash"
        ) is False


class TestParseEvaluationResponse:
    """Tests for _parse_evaluation_response."""

    def test_valid_json(self) -> None:
        """Parse valid JSON evaluation response."""
        content = json.dumps({
            "scores": {"staleness": 85, "relevance": 30},
            "suggested_action": "close_stale",
            "suggested_action_reason": "No activity for 6 months.",
        })

        result = _parse_evaluation_response(content)

        assert result["scores"]["staleness"] == 85
        assert result["suggested_action"] == "close_stale"
        assert "No activity" in result["suggested_action_reason"]

    def test_json_wrapped_in_markdown(self) -> None:
        """Parse JSON wrapped in markdown code fences."""
        content = '```json\n{"scores": {"staleness": 50}, "suggested_action": "keep_open", "suggested_action_reason": "Active."}\n```'

        result = _parse_evaluation_response(content)

        assert result["scores"]["staleness"] == 50

    def test_invalid_json_returns_none(self) -> None:
        """Invalid JSON returns None."""
        result = _parse_evaluation_response("This is not JSON at all.")

        assert result is None


class TestIssueEvaluator:
    """Tests for IssueEvaluator."""

    def test_init(self) -> None:
        """IssueEvaluator initializes with client and model config."""
        mock_client = MagicMock()
        evaluator = IssueEvaluator(
            client=mock_client,
            summary_model="google/gemini-flash-1.5",
            evaluation_model="anthropic/claude-sonnet-4-20250514",
        )

        assert evaluator.summary_model == "google/gemini-flash-1.5"
        assert evaluator.evaluation_model == "anthropic/claude-sonnet-4-20250514"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/llm/test_evaluator.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

Create `craft_dashboard/llm/evaluator.py`:
```python
"""Issue and PR evaluator using LLM."""

import hashlib
import json
import logging
from datetime import datetime, timezone

from craft_dashboard.llm.client import OpenRouterClient, OpenRouterResponse
from craft_dashboard.llm.prompts import build_evaluation_prompt, build_summary_prompt

logger = logging.getLogger(__name__)


def _needs_reevaluation(
    existing_hash: str | None,
    current_hash: str,
) -> bool:
    """Check if an issue needs re-evaluation based on content hash.

    Args:
        existing_hash: Hash from the last evaluation, or None if never evaluated.
        current_hash: Hash of the current issue content.

    Returns:
        True if the issue needs re-evaluation.
    """
    if existing_hash is None:
        return True
    return existing_hash != current_hash


def _parse_evaluation_response(content: str) -> dict | None:
    """Parse the LLM evaluation response as JSON.

    Handles responses that may be wrapped in markdown code fences.

    Args:
        content: Raw LLM response content.

    Returns:
        Parsed dict with scores and action, or None if parsing fails.
    """
    # Strip markdown code fences if present
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (the fences)
        cleaned = "\n".join(lines[1:-1]).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM evaluation response as JSON")
        return None


def _compute_content_hash(
    title: str,
    body: str | None,
    state: str,
    labels: list[str],
) -> str:
    """Compute a SHA-256 hash of issue content for change detection.

    Args:
        title: Issue title.
        body: Issue body text.
        state: Issue state.
        labels: List of label names.

    Returns:
        A 64-character hex string.
    """
    content = f"{title}|{body or ''}|{state}|{','.join(sorted(labels))}"
    return hashlib.sha256(content.encode()).hexdigest()


class IssueEvaluator:
    """Evaluates issues and PRs using an LLM via OpenRouter."""

    def __init__(
        self,
        client: OpenRouterClient,
        summary_model: str = "google/gemini-flash-1.5",
        evaluation_model: str = "anthropic/claude-sonnet-4-20250514",
    ) -> None:
        """Initialize the evaluator.

        Args:
            client: OpenRouter HTTP client.
            summary_model: Model to use for summarization (cheaper).
            evaluation_model: Model to use for scoring (more capable).
        """
        self.client = client
        self.summary_model = summary_model
        self.evaluation_model = evaluation_model

    async def evaluate_issue(
        self,
        *,
        title: str,
        body: str | None,
        issue_type: str,
        labels: list[str],
        age_days: int,
        last_activity_days: int,
        author: str,
        is_maintainer: bool,
        comment_count: int,
        existing_hash: str | None = None,
    ) -> dict | None:
        """Evaluate a single issue or PR.

        Args:
            title: Issue/PR title.
            body: Issue/PR body text.
            issue_type: 'issue' or 'pull_request'.
            labels: List of label names.
            age_days: Days since creation.
            last_activity_days: Days since last update.
            author: Author username.
            is_maintainer: Whether the author is a project maintainer.
            comment_count: Number of comments.
            existing_hash: Content hash from previous evaluation, if any.

        Returns:
            Dict with summary, scores, action, tokens, and hash. None if skipped.
        """
        label_names = labels if isinstance(labels, list) else []
        current_hash = _compute_content_hash(title, body, "open", label_names)

        if not _needs_reevaluation(existing_hash, current_hash):
            logger.debug("Skipping evaluation (content unchanged): %s", title)
            return None

        total_tokens = 0

        # Step 1: Summarize (cheap model)
        summary_messages = build_summary_prompt(
            title=title,
            body=body,
            issue_type=issue_type,
            labels=label_names,
        )
        summary_response = await self.client.chat(
            model=self.summary_model,
            messages=summary_messages,
            max_tokens=256,
        )
        summary = summary_response.content
        total_tokens += summary_response.total_tokens

        # Step 2: Evaluate and score (more capable model)
        eval_messages = build_evaluation_prompt(
            title=title,
            body=body,
            issue_type=issue_type,
            labels=label_names,
            age_days=age_days,
            last_activity_days=last_activity_days,
            author=author,
            is_maintainer=is_maintainer,
            comment_count=comment_count,
        )
        eval_response = await self.client.chat(
            model=self.evaluation_model,
            messages=eval_messages,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        total_tokens += eval_response.total_tokens

        parsed = _parse_evaluation_response(eval_response.content)
        if parsed is None:
            logger.warning("Could not parse evaluation for: %s", title)
            return {
                "summary": summary,
                "scores": {},
                "suggested_action": None,
                "suggested_action_reason": None,
                "tokens_used": total_tokens,
                "issue_data_hash": current_hash,
            }

        return {
            "summary": summary,
            "scores": parsed.get("scores", {}),
            "suggested_action": parsed.get("suggested_action"),
            "suggested_action_reason": parsed.get("suggested_action_reason"),
            "tokens_used": total_tokens,
            "issue_data_hash": current_hash,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/llm/test_evaluator.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add craft_dashboard/llm/evaluator.py tests/unit/llm/test_evaluator.py
git commit -m "feat: add issue evaluator with summarization and scoring"
```

---

### Task 4: LLM Evaluation Cron Script

**Files:**
- Create: `scripts/run_llm.py`

- [ ] **Step 1: Create the cron-friendly LLM evaluation script**

Create `scripts/run_llm.py`:
```python
#!/usr/bin/env python3
"""LLM evaluation entry point for cron jobs.

Evaluates issues/PRs that have changed since last evaluation.
By default evaluates all issues (open and closed). Use --open-only
for the daily cron job. Use --backend local for a local LLM server.

Usage:
    uv run scripts/run_llm.py                          # all issues, openrouter
    uv run scripts/run_llm.py --backend local          # all issues, local LLM server
    uv run scripts/run_llm.py --open-only              # open issues only (cron mode)
    uv run scripts/run_llm.py --project snapcraft
    uv run scripts/run_llm.py --limit 100

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
    LLM_BACKEND: "openrouter" or "local" (default: openrouter)
    OPENROUTER_API_KEY: OpenRouter API key (required when LLM_BACKEND=openrouter)
    LOCAL_LLM_URL: Local LLM base URL (default: http://localhost:11434/v1)
"""

import asyncio
import logging
import pathlib
import sys
from datetime import datetime, timezone

import click

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.llm.client import OpenRouterClient
from craft_dashboard.llm.evaluator import IssueEvaluator
from craft_dashboard.llm.client import QuotaExhaustedError, create_llm_client
from craft_dashboard.settings import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _evaluate_issues(
    session_factory,
    evaluator: IssueEvaluator,
    maintainers: set[str],
    project_filter: str = "",
    limit: int = 0,
    open_only: bool = False,
) -> dict[str, int]:
    """Evaluate issues/PRs that need re-evaluation.

    By default evaluates all issues (open and closed). Pass open_only=True
    for the daily cron run which only needs to catch newly-changed open issues.

    Args:
        session_factory: Async session factory.
        evaluator: The IssueEvaluator instance.
        maintainers: Set of maintainer usernames.
        project_filter: Optional project name filter.
        limit: Max issues to evaluate (0 = unlimited).
        open_only: If True, only evaluate open issues.

    Returns:
        Stats dict with evaluated, skipped, errored counts.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.llm_evaluation import LLMEvaluation
    from craft_dashboard.models.project import Project

    stats = {"evaluated": 0, "skipped": 0, "errored": 0, "total_tokens": 0}

    async with session_factory() as session:
        query = (
            select(
                Issue,
                Project.name.label("project_name"),
                LLMEvaluation.issue_data_hash,
            )
            .join(Project, Issue.project_id == Project.id)
            .outerjoin(
                LLMEvaluation,
                (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest.is_(True),
            )
        )

        if open_only:
            query = query.where(Issue.state == "open")

        if project_filter:
            query = query.where(Project.name == project_filter)

        if limit > 0:
            query = query.limit(limit)

        result = await session.execute(query)
        rows = result.all()

    for row in rows:
        issue = row[0]
        existing_hash = row.issue_data_hash
        labels = issue.labels if isinstance(issue.labels, list) else []

        now = datetime.now(tz=timezone.utc)
        created = issue.created_at or now
        updated = issue.updated_at or now
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)

        age_days = (now - created).days
        last_activity_days = (now - updated).days

        try:
            result = await evaluator.evaluate_issue(
                title=issue.title,
                body=issue.body,
                issue_type=issue.issue_type,
                labels=labels,
                age_days=age_days,
                last_activity_days=last_activity_days,
                author=issue.author or "unknown",
                is_maintainer=issue.author in maintainers if issue.author else False,
                comment_count=0,  # Could be fetched from metadata
                existing_hash=existing_hash,
            )
        except QuotaExhaustedError:
            logger.warning(
                "OpenRouter daily quota exhausted. Stopping evaluation. "
                "%d evaluated so far.",
                stats["evaluated"],
            )
            break
        except Exception:
            logger.exception("Error evaluating issue %s", issue.title)
            stats["errored"] += 1
            continue

        if result is None:
            stats["skipped"] += 1
            continue

        # Upsert evaluation: mark previous evaluation as not-latest, then insert new one
        async with session_factory() as session:
            # Mark previous evaluation(s) for this issue as not latest
            from sqlalchemy import update as sa_update
            await session.execute(
                sa_update(LLMEvaluation)
                .where(LLMEvaluation.issue_id == issue.id, LLMEvaluation.latest.is_(True))
                .values(latest=False)
            )
            # Insert new evaluation with latest=True
            await session.execute(
                insert(LLMEvaluation).values(
                    issue_id=issue.id,
                    model_name=evaluator.evaluation_model,
                    summary=result["summary"],
                    suggested_action=result["suggested_action"],
                    suggested_action_reason=result["suggested_action_reason"],
                    scores=result["scores"],
                    tokens_used=result["tokens_used"],
                    evaluated_at=datetime.now(tz=timezone.utc),
                    issue_data_hash=result["issue_data_hash"],
                    latest=True,
                )
            )
            await session.commit()

        stats["evaluated"] += 1
        stats["total_tokens"] += result["tokens_used"]
        logger.info(
            "Evaluated: %s (%s, %d tokens)",
            issue.title[:60],
            result["suggested_action"],
            result["tokens_used"],
        )

    return stats


async def _main(project: str, limit: int, backend: str, open_only: bool) -> None:
    """Run LLM evaluation."""
    settings = Settings()

    # Allow CLI flag to override settings
    if backend:
        settings = settings.model_copy(update={"llm_backend": backend})

    if settings.llm_backend == "openrouter" and not settings.openrouter_api_key:
        logger.error("OPENROUTER_API_KEY environment variable is not set.")
        sys.exit(1)

    config = load_config(pathlib.Path(settings.config_file))
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)

    client = create_llm_client(settings)

    # Select model names based on backend
    if settings.llm_backend == "local":
        summary_model = settings.local_llm_summary_model
        evaluation_model = settings.local_llm_evaluation_model
    else:
        summary_model = settings.openrouter_summary_model
        evaluation_model = settings.openrouter_evaluation_model

    evaluator = IssueEvaluator(
        client=client,
        summary_model=summary_model,
        evaluation_model=evaluation_model,
    )
    logger.info(
        "Using %s backend (summary=%s, eval=%s, open_only=%s)",
        settings.llm_backend,
        summary_model,
        evaluation_model,
        open_only,
    )

    try:
        stats = await _evaluate_issues(
            session_factory=session_factory,
            evaluator=evaluator,
            maintainers=set(config.maintainers),
            project_filter=project,
            limit=limit,
            open_only=open_only,
        )
        logger.info(
            "Evaluation complete: %d evaluated, %d skipped, %d errors, %d total tokens",
            stats["evaluated"],
            stats["skipped"],
            stats["errored"],
            stats["total_tokens"],
        )
    finally:
        await engine.dispose()


@click.command()
@click.option("--project", default="", help="Only evaluate issues for this project.")
@click.option("--limit", default=0, type=int, help="Max issues to evaluate (0=all).")
@click.option(
    "--backend",
    type=click.Choice(["openrouter", "local"]),
    default="",
    help="LLM backend to use (overrides LLM_BACKEND env var).",
)
@click.option(
    "--open-only",
    is_flag=True,
    default=False,
    help="Only evaluate open issues (used for daily cron; default evaluates all).",
)
def main(project: str, limit: int, backend: str, open_only: bool) -> None:
    """Run LLM evaluation on issues and PRs.

    By default evaluates all issues (open and closed). Use --open-only
    for the daily cron job which only needs to catch changed open issues.
    Use --backend local to run against a locally-hosted OpenAI-compatible LLM server
    without spending tokens on OpenRouter.
    """
    asyncio.run(_main(project, limit, backend, open_only))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/run_llm.py
git commit -m "feat: add LLM evaluation cron entry point script"
```

---

### Task 5: Run Full Test Suite and Lint

**Files:**
- No new files

- [ ] **Step 1: Run the full test suite**

Run: `make test`
Expected: All tests PASS

- [ ] **Step 2: Format and lint**

Run: `make format && make lint`
Expected: No errors

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "chore: lint and format pass for LLM integration"
```
