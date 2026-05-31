# Duplicate Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect duplicate issues across a project by comparing embeddings and confirming with an LLM, then populate the `duplicateness` score and optionally rewrite the summary.

**Architecture:** Two-phase evaluation. Phase 1 (existing) produces a summary and scores for each issue independently. Phase 2 (new) runs after phase 1: it embeds the summary, searches for nearest neighbours, and asks the LLM to confirm or reject candidate duplicates. If confirmed, it updates the `duplicateness` score and rewrites the summary to reference the duplicate(s). This design keeps phase 1 cheap and fast, while phase 2 only fires LLM calls for issues with high embedding similarity.

**Tech Stack:** PostgreSQL with pgvector extension, sentence-transformers (or an OpenAI-compatible `/v1/embeddings` endpoint), existing LLM client (LocalLLMClient / OpenRouterClient).

---

## Context

### Current architecture

- `IssueEvaluator.evaluate_issue()` calls `_summarize()` then `_score()` per issue.
- Each issue is evaluated in isolation — the LLM never sees other issues.
- The `duplicateness` score was removed from the evaluation prompt because a single-issue LLM call cannot meaningfully score it.
- The DB column and UI column still exist and accept `None` (nullable float).

### Why embeddings + LLM verification

Embeddings alone give fast similarity search (O(1) per query with HNSW index) but can't confirm semantic duplicates (similar symptoms ≠ same bug). An LLM can confirm, but comparing every pair is O(n²). The hybrid approach: embeddings narrow candidates to top-k, then the LLM confirms/rejects each candidate pair. This is O(k) LLM calls per issue, where k is small (3–5).

### Design decisions

1. **Embeddings live on the evaluation, not the issue.** Issues can be re-summarized; the embedding should match the current summary. Store on `llm_evaluations` alongside the summary.
2. **Phase 2 is a separate pass**, not part of `evaluate_issue()`. It runs after all phase-1 evaluations complete, or as a dedicated subcommand/flag.
3. **Cross-project duplicates are out of scope** for v1. Duplicates are scoped to a single project.
4. **Summary rewrite is opt-in.** When a duplicate is confirmed, the summary can be rewritten to mention the duplicate (e.g., "Likely duplicate of #1234. Original: ..."). This is a separate LLM call using the existing summary model.

---

## File structure

| File | Responsibility |
| --- | --- |
| `craft_dashboard/models/llm_evaluation.py` | Add `summary_embedding` vector column |
| `craft_dashboard/llm/embeddings.py` (new) | Embedding client: compute embeddings via `/v1/embeddings` |
| `craft_dashboard/llm/duplicate_detector.py` (new) | Phase 2 logic: embed, search, confirm, update |
| `craft_dashboard/llm/prompts.py` | Add duplicate confirmation and summary rewrite prompts |
| `craft_dashboard/repositories/evaluation_repository.py` | Add nearest-neighbour query method |
| `scripts/eval_client.py` | Add `--detect-duplicates` flag to run phase 2 after phase 1 |
| `alembic/versions/XXXX_add_embedding_column.py` | Migration: add pgvector extension + column |
| Tests for each of the above |

---

### Task 1: Add pgvector support and embedding column

**Files:**
- Modify: `pyproject.toml` — add `pgvector` dependency
- Modify: `craft_dashboard/models/llm_evaluation.py` — add `summary_embedding` column
- Create: `alembic/versions/XXXX_add_summary_embedding.py` — migration

- [ ] **Step 1: Add pgvector dependency**

```toml
# In pyproject.toml [project.dependencies], add:
"pgvector>=0.3,<1",
```

Run: `uv sync`

- [ ] **Step 2: Add the embedding column to the model**

In `craft_dashboard/models/llm_evaluation.py`, add the import and column:

```python
from pgvector.sqlalchemy import Vector

# Inside LLMEvaluation class, after eval_locked_until:
summary_embedding: Mapped[list[float] | None] = mapped_column(
    Vector(384), nullable=True
)
```

The dimension (384) matches `all-MiniLM-L6-v2`, a small, fast embedding model suitable for similarity search. If a different model is used, this dimension must match.

- [ ] **Step 3: Create the Alembic migration**

```bash
uv run alembic revision --autogenerate -m "add summary_embedding column"
```

Review the generated migration. It should:
1. Execute `CREATE EXTENSION IF NOT EXISTS vector` (pgvector).
2. Add the `summary_embedding` column to `llm_evaluations`.
3. Create an HNSW index for cosine similarity search:

```python
op.execute("CREATE EXTENSION IF NOT EXISTS vector")
op.add_column("llm_evaluations", sa.Column("summary_embedding", Vector(384)))
op.execute(
    "CREATE INDEX ix_llm_evaluations_embedding ON llm_evaluations "
    "USING hnsw (summary_embedding vector_cosine_ops) WHERE latest = true"
)
```

- [ ] **Step 4: Run the migration locally**

```bash
uv run alembic upgrade head
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: add pgvector embedding column to llm_evaluations"
```

---

### Task 2: Embedding client

**Files:**
- Create: `craft_dashboard/llm/embeddings.py`
- Create: `tests/unit/llm/test_embeddings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/llm/test_embeddings.py
import httpx
import pytest
from craft_dashboard.llm.embeddings import EmbeddingClient


@pytest.mark.asyncio
async def test_embed_returns_vector(respx_mock):
    respx_mock.post("http://localhost:11434/v1/embeddings").respond(
        json={
            "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}],
            "model": "all-minilm",
            "usage": {"prompt_tokens": 10, "total_tokens": 10},
        }
    )
    client = EmbeddingClient(base_url="http://localhost:11434/v1")
    try:
        result = await client.embed("test text")
    finally:
        await client.close()
    assert result == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_batch(respx_mock):
    respx_mock.post("http://localhost:11434/v1/embeddings").respond(
        json={
            "data": [
                {"embedding": [0.1, 0.2], "index": 0},
                {"embedding": [0.3, 0.4], "index": 1},
            ],
            "model": "all-minilm",
            "usage": {"prompt_tokens": 20, "total_tokens": 20},
        }
    )
    client = EmbeddingClient(base_url="http://localhost:11434/v1")
    try:
        results = await client.embed_batch(["text a", "text b"])
    finally:
        await client.close()
    assert len(results) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/llm/test_embeddings.py -v
```

- [ ] **Step 3: Implement EmbeddingClient**

```python
# craft_dashboard/llm/embeddings.py
"""Client for computing text embeddings via an OpenAI-compatible API."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Compute embeddings using an OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434/v1",
        model: str = "all-minilm",
        api_key: str = "",
        ca_cert: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.ca_cert = ca_cert
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            import pathlib

            verify: bool | str = (
                str(pathlib.Path(self.ca_cert).expanduser())
                if self.ca_cert
                else True
            )
            self._http = httpx.AsyncClient(timeout=60.0, verify=verify)
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    async def embed(self, text: str) -> list[float]:
        """Compute an embedding for a single text string."""
        result = await self.embed_batch([text])
        return result[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Compute embeddings for multiple texts in one API call."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response = await self.http.post(
            f"{self.base_url}/embeddings",
            headers=headers,
            json={"model": self.model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()
        # Sort by index to guarantee order matches input
        sorted_data = sorted(data["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in sorted_data]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/llm/test_embeddings.py -v
```

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: add embedding client for duplicate detection"
```

---

### Task 3: Nearest-neighbour query in the evaluation repository

**Files:**
- Modify: `craft_dashboard/repositories/evaluation_repository.py`
- Create: `tests/integration/test_embedding_search.py`

This task adds a method to find evaluations whose embeddings are closest to a given vector, scoped to a project.

- [ ] **Step 1: Write the failing test**

The test needs a running PostgreSQL with pgvector. Use the existing integration test fixtures.

```python
# tests/integration/test_embedding_search.py
import pytest
from craft_dashboard.repositories.evaluation_repository import find_similar_evaluations


@pytest.mark.asyncio
async def test_find_similar_returns_closest_matches(test_db_session):
    """Seed 3 evaluations with embeddings, query, and verify ordering."""
    # Seed issues + evaluations with known embeddings
    # ...
    results = await find_similar_evaluations(
        session=test_db_session,
        project_id=1,
        embedding=[1.0, 0.0, 0.0],  # unit vector
        exclude_issue_id=99,  # exclude the query issue itself
        limit=2,
    )
    assert len(results) <= 2
    # Results should be ordered by cosine similarity (most similar first)
```

- [ ] **Step 2: Implement the query**

```python
# In craft_dashboard/repositories/evaluation_repository.py

async def find_similar_evaluations(
    *,
    session: AsyncSession,
    project_id: int,
    embedding: list[float],
    exclude_issue_id: int,
    limit: int = 5,
    threshold: float = 0.15,
) -> list[dict]:
    """Find evaluations with similar summary embeddings.

    Uses pgvector cosine distance (<=> operator). Lower distance = more similar.
    threshold is the maximum cosine distance (0 = identical, 2 = opposite).

    Returns list of dicts: issue_id, external_id, title, summary, distance.
    """
    from sqlalchemy import text as sa_text

    query = sa_text("""
        SELECT e.issue_id, i.external_id, i.title, e.summary,
               e.summary_embedding <=> :embedding AS distance
        FROM llm_evaluations e
        JOIN issues i ON e.issue_id = i.id
        WHERE e.latest = true
          AND e.summary_embedding IS NOT NULL
          AND i.project_id = :project_id
          AND i.id != :exclude_issue_id
          AND (e.summary_embedding <=> :embedding) < :threshold
        ORDER BY distance
        LIMIT :limit
    """)
    result = await session.execute(
        query,
        {
            "embedding": str(embedding),
            "project_id": project_id,
            "exclude_issue_id": exclude_issue_id,
            "threshold": threshold,
            "limit": limit,
        },
    )
    return [dict(row._mapping) for row in result.fetchall()]
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/integration/test_embedding_search.py -v
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: add nearest-neighbour embedding search"
```

---

### Task 4: Duplicate confirmation prompt

**Files:**
- Modify: `craft_dashboard/llm/prompts.py`
- Create: `tests/unit/llm/test_duplicate_prompts.py`

- [ ] **Step 1: Add the duplicate confirmation prompt**

```python
# In craft_dashboard/llm/prompts.py

_DUPLICATE_CHECK_SYSTEM = """\
You are an expert open-source project maintainer. Given two issues from the \
same project, determine whether they describe the same underlying problem or \
feature request.

Two issues are duplicates if they describe the same root cause, bug, or \
feature — even if the symptoms, wording, or reproduction steps differ.

Two issues are NOT duplicates if they merely involve the same component or \
area of the codebase but describe distinct problems.

Respond with valid JSON:
{
  "is_duplicate": <true or false>,
  "confidence": <0-100, how confident you are>,
  "reason": "<brief explanation>"
}
"""


_SUMMARY_REWRITE_SYSTEM = """\
You are a concise technical writer. Rewrite the following issue summary to \
note that it is likely a duplicate. Prepend "May duplicate of #<number>, #<number>. " \
to a condensed version of the original summary. Keep it under 300 characters. \
Do not include markdown formatting.
"""


def build_duplicate_check_prompt(
    *,
    issue_a_title: str,
    issue_a_summary: str,
    issue_b_title: str,
    issue_b_summary: str,
    issue_b_external_id: str,
) -> list[dict[str, str]]:
    """Build a prompt to check if two issues are duplicates."""
    user_content = (
        f"Issue A:\n"
        f"  Title: {issue_a_title}\n"
        f"  Summary: {issue_a_summary}\n\n"
        f"Issue B (#{issue_b_external_id}):\n"
        f"  Title: {issue_b_title}\n"
        f"  Summary: {issue_b_summary}\n"
    )
    return [
        {"role": "system", "content": _DUPLICATE_CHECK_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_duplicate_summary_rewrite_prompt(
    *,
    original_summary: str,
    duplicate_external_id: str,
) -> list[dict[str, str]]:
    """Build a prompt to rewrite a summary noting the duplicate."""
    user_content = (
        f"Original summary: {original_summary}\n"
        f"Duplicate of: #{duplicate_external_id}\n"
    )
    return [
        {"role": "system", "content": _SUMMARY_REWRITE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
```

- [ ] **Step 2: Write tests for prompt construction**

```python
# tests/unit/llm/test_duplicate_prompts.py
from craft_dashboard.llm.prompts import (
    build_duplicate_check_prompt,
    build_duplicate_summary_rewrite_prompt,
)


def test_duplicate_check_prompt_includes_both_issues():
    messages = build_duplicate_check_prompt(
        issue_a_title="Build fails on core24",
        issue_a_summary="Core24 build pipeline broken",
        issue_b_title="core24 snap build error",
        issue_b_summary="Snap build errors on core24 base",
        issue_b_external_id="1234",
    )
    assert len(messages) == 2
    assert "Issue A:" in messages[1]["content"]
    assert "Issue B (#1234):" in messages[1]["content"]


def test_summary_rewrite_prompt_includes_original():
    messages = build_duplicate_summary_rewrite_prompt(
        original_summary="Core24 build pipeline broken",
        duplicate_external_id="1234",
    )
    assert len(messages) == 2
    assert "Core24 build pipeline broken" in messages[1]["content"]
    assert "#1234" in messages[1]["content"]
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/llm/test_duplicate_prompts.py -v
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: add duplicate confirmation and summary rewrite prompts"
```

---

### Task 5: Duplicate detector (phase 2 orchestrator)

**Files:**
- Create: `craft_dashboard/llm/duplicate_detector.py`
- Create: `tests/unit/llm/test_duplicate_detector.py`

This is the core orchestration: for a given issue, embed its summary, find neighbours, confirm with LLM, and update the evaluation.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/llm/test_duplicate_detector.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from craft_dashboard.llm.duplicate_detector import DuplicateDetector


@pytest.mark.asyncio
async def test_detect_skips_when_no_similar_issues():
    embedding_client = AsyncMock()
    embedding_client.embed.return_value = [0.1, 0.2, 0.3]
    llm_client = AsyncMock()
    detector = DuplicateDetector(
        embedding_client=embedding_client,
        llm_client=llm_client,
        evaluation_model="test-model",
        summary_model="test-model",
    )
    # No similar issues found
    find_similar = AsyncMock(return_value=[])
    result = await detector.check_duplicates(
        issue_id=1,
        project_id=1,
        title="Test issue",
        summary="Test summary",
        find_similar_fn=find_similar,
    )
    assert result is None
    llm_client.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_returns_duplicate_when_llm_confirms():
    embedding_client = AsyncMock()
    embedding_client.embed.return_value = [0.1, 0.2, 0.3]
    llm_client = AsyncMock()
    llm_client.complete.return_value = MagicMock(
        content='{"is_duplicate": true, "confidence": 85, "reason": "Same root cause"}'
    )
    find_similar = AsyncMock(return_value=[
        {
            "issue_id": 2,
            "external_id": "42",
            "title": "Similar issue",
            "summary": "Similar summary",
            "distance": 0.05,
        }
    ])
    detector = DuplicateDetector(
        embedding_client=embedding_client,
        llm_client=llm_client,
        evaluation_model="test-model",
        summary_model="test-model",
    )
    result = await detector.check_duplicates(
        issue_id=1,
        project_id=1,
        title="Test issue",
        summary="Test summary",
        find_similar_fn=find_similar,
    )
    assert result is not None
    assert result["duplicate_of_external_id"] == "42"
    assert result["confidence"] == 85
```

- [ ] **Step 2: Implement DuplicateDetector**

```python
# craft_dashboard/llm/duplicate_detector.py
"""Phase 2 duplicate detection: embed, search, confirm, update."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Awaitable

from craft_dashboard.llm.client import LLMClient
from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.llm.prompts import (
    build_duplicate_check_prompt,
    build_duplicate_summary_rewrite_prompt,
)

logger = logging.getLogger(__name__)

FindSimilarFn = Callable[..., Awaitable[list[dict[str, Any]]]]


class DuplicateDetector:
    """Detect duplicate issues using embeddings + LLM confirmation."""

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient,
        llm_client: LLMClient,
        evaluation_model: str,
        summary_model: str,
        confidence_threshold: int = 70,
    ) -> None:
        self.embedding_client = embedding_client
        self.llm_client = llm_client
        self.evaluation_model = evaluation_model
        self.summary_model = summary_model
        self.confidence_threshold = confidence_threshold

    async def check_duplicates(
        self,
        *,
        issue_id: int,
        project_id: int,
        title: str,
        summary: str,
        find_similar_fn: FindSimilarFn,
    ) -> dict[str, Any] | None:
        """Check if an issue is a duplicate of any existing issue.

        Returns a dict with duplicate info if confirmed, None otherwise.
        """
        embedding = await self.embedding_client.embed(summary)

        candidates = await find_similar_fn(
            project_id=project_id,
            embedding=embedding,
            exclude_issue_id=issue_id,
        )

        if not candidates:
            return None

        for candidate in candidates:
            messages = build_duplicate_check_prompt(
                issue_a_title=title,
                issue_a_summary=summary,
                issue_b_title=candidate["title"],
                issue_b_summary=candidate["summary"],
                issue_b_external_id=candidate["external_id"],
            )
            response = await self.llm_client.complete(
                model=self.evaluation_model,
                messages=messages,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            parsed = self._parse_response(response.content)
            if (
                parsed
                and parsed.get("is_duplicate")
                and parsed.get("confidence", 0) >= self.confidence_threshold
            ):
                return {
                    "duplicate_of_issue_id": candidate["issue_id"],
                    "duplicate_of_external_id": candidate["external_id"],
                    "confidence": parsed["confidence"],
                    "reason": parsed.get("reason", ""),
                    "embedding": embedding,
                }

        # No confirmed duplicate, but still return the embedding for storage
        return None

    async def rewrite_summary(
        self,
        *,
        original_summary: str,
        duplicate_external_id: str,
    ) -> str:
        """Rewrite a summary to note the detected duplicate."""
        messages = build_duplicate_summary_rewrite_prompt(
            original_summary=original_summary,
            duplicate_external_id=duplicate_external_id,
        )
        response = await self.llm_client.complete(
            model=self.summary_model,
            messages=messages,
            max_tokens=512,
        )
        content = re.sub(
            r"<think>.*?</think>", "", response.content, flags=re.DOTALL
        ).strip()
        return content

    @staticmethod
    def _parse_response(content: str) -> dict[str, Any] | None:
        cleaned = re.sub(
            r"<think>.*?</think>", "", content, flags=re.DOTALL
        ).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        brace_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning("Failed to parse duplicate check response: %s", cleaned[:200])
        return None
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/llm/test_duplicate_detector.py -v
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: add duplicate detector with embedding search + LLM confirmation"
```

---

### Task 6: Wire phase 2 into the eval client

**Files:**
- Modify: `scripts/eval_client.py` — add `--detect-duplicates` flag
- Modify: `tests/unit/scripts/test_eval_client.py`

- [ ] **Step 1: Add the flag and phase-2 call**

After a successful phase-1 evaluation and submission, if `--detect-duplicates` is set, run the duplicate detector. If a duplicate is confirmed:
1. Update `duplicateness` score to the confidence value.
2. Optionally rewrite the summary.
3. Re-submit the updated evaluation.

```python
# In eval_client.py, add click option:
@click.option(
    "--detect-duplicates",
    is_flag=True,
    default=False,
    help="Run phase-2 duplicate detection after evaluation",
)
```

The phase-2 flow in the eval loop, after a successful phase-1 submit:

```python
if detect_duplicates and result.get("summary"):
    dup_result = await duplicate_detector.check_duplicates(
        issue_id=issue_data["issue_id"],
        project_id=issue_data["project_id"],
        title=issue_data["title"],
        summary=result["summary"],
        find_similar_fn=...,  # bound to the DB session
    )
    if dup_result:
        logger.info(
            "%s: duplicate of #%s (confidence %d%%)",
            issue_ref,
            dup_result["duplicate_of_external_id"],
            dup_result["confidence"],
        )
        # Update scores and re-submit
        submission["scores"]["duplicateness"] = dup_result["confidence"]
        rewritten = await duplicate_detector.rewrite_summary(
            original_summary=result["summary"],
            duplicate_external_id=dup_result["duplicate_of_external_id"],
        )
        submission["summary"] = rewritten
        # Submit the updated evaluation
        await http_client.post("/api/eval/result", json=submission, headers=headers)
```

- [ ] **Step 2: Write tests for the new flag**

Test that `--detect-duplicates` triggers the detector and that the result is re-submitted.

- [ ] **Step 3: Run tests**

```bash
make format && make lint && make test
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: wire duplicate detection into eval client"
```

---

### Task 7: Backfill embeddings for existing evaluations

**Files:**
- Create: `scripts/backfill_embeddings.py`

A one-off script that reads all evaluations with `summary_embedding IS NULL` and `summary IS NOT NULL`, computes embeddings in batches, and writes them back.

- [ ] **Step 1: Write the script**

```python
# scripts/backfill_embeddings.py
"""Backfill summary embeddings for existing evaluations."""
# Reads evaluations in batches of 100, computes embeddings, updates rows.
# Uses the same EmbeddingClient as the eval client.
# Idempotent: skips rows that already have embeddings.
```

- [ ] **Step 2: Test locally**

```bash
uv run scripts/backfill_embeddings.py --limit 10 --dry-run
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat: add backfill script for summary embeddings"
```

---

## Open questions

1. **Embedding model choice.** `all-MiniLM-L6-v2` (384 dims) is fast and small. `nomic-embed-text` via Ollama is another option. The dimension in the Vector column must match.
2. **Similarity threshold.** The cosine distance threshold (0.15 default) needs tuning. Too low = misses real duplicates; too high = too many LLM calls.
3. **Phase 2 timing.** Should it run inline after each phase-1 eval, or as a separate batch pass? Inline is simpler but slower per issue. A separate `--detect-duplicates-only` mode that scans all issues without re-evaluating could be useful.
4. **Cross-project duplicates.** Some issues span projects (e.g., snapcraft and craft-parts). v1 is per-project; cross-project could be a future extension.
