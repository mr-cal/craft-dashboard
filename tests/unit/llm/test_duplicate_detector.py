"""Tests for DuplicateDetector."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from craft_dashboard.llm.client import LLMResponse
from craft_dashboard.llm.duplicate_detector import (
    DuplicateDetector,
    _parse_json_response,
)


def _make_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        model="test-model",
    )


def _make_detector(
    llm_content: str = "",
) -> tuple[DuplicateDetector, AsyncMock, AsyncMock]:
    embedding_client = AsyncMock()
    embedding_client.embed.return_value = [0.1, 0.2, 0.3]
    llm_client = AsyncMock()
    if llm_content:
        llm_client.complete.return_value = _make_response(llm_content)
    detector = DuplicateDetector(
        embedding_client=embedding_client,
        llm_client=llm_client,
        model="eval-model",
    )
    return detector, embedding_client, llm_client


@pytest.mark.asyncio
async def test_check_duplicates_returns_none_when_no_candidates():
    detector, _emb, llm_client = _make_detector()
    find_similar = AsyncMock(return_value=[])

    result = await detector.check_duplicates(
        issue_id=1,
        project_name="snapcraft",
        title="Build fails",
        summary="Build failure",
        embedding=[0.1, 0.2, 0.3],
        find_similar_fn=find_similar,
    )

    assert result == {"candidates_compared": 0}
    llm_client.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_duplicates_returns_duplicate_when_confirmed():
    detector, _emb, llm_client = _make_detector(
        '{"is_duplicate": true, "confidence": 85, "reason": "Same root cause"}'
    )
    candidates = [
        {
            "issue_id": 2,
            "external_id": "42",
            "project_name": "snapcraft",
            "title": "Similar issue",
            "summary": "Similar summary",
            "distance": 0.05,
        }
    ]
    find_similar = AsyncMock(return_value=candidates)

    result = await detector.check_duplicates(
        issue_id=1,
        project_name="snapcraft",
        title="Build fails",
        summary="Build failure",
        embedding=[0.1, 0.2, 0.3],
        find_similar_fn=find_similar,
    )

    assert result is not None
    assert result["duplicate_of_issue_id"] == 2
    assert result["duplicate_of_external_id"] == "42"
    assert result["confidence"] == 85
    assert result["candidates_compared"] == 1


@pytest.mark.asyncio
async def test_check_duplicates_skips_low_confidence():
    detector, _emb, llm_client = _make_detector(
        '{"is_duplicate": true, "confidence": 50, "reason": "Maybe"}'
    )
    candidates = [
        {
            "issue_id": 2,
            "external_id": "42",
            "project_name": "snapcraft",
            "title": "Maybe similar",
            "summary": "Somewhat similar",
            "distance": 0.1,
        }
    ]
    find_similar = AsyncMock(return_value=candidates)

    result = await detector.check_duplicates(
        issue_id=1,
        project_name="snapcraft",
        title="Build fails",
        summary="Build failure",
        embedding=[0.1, 0.2, 0.3],
        find_similar_fn=find_similar,
    )

    # confidence 50 < threshold 70, so no duplicate found
    assert "duplicate_of_issue_id" not in (result or {})
    assert result == {"candidates_compared": 1}


@pytest.mark.asyncio
async def test_check_duplicates_skips_when_llm_says_no():
    detector, _emb, llm_client = _make_detector(
        '{"is_duplicate": false, "confidence": 95, "reason": "Different problems"}'
    )
    candidates = [
        {
            "issue_id": 2,
            "external_id": "42",
            "project_name": "snapcraft",
            "title": "Different issue",
            "summary": "Different thing",
            "distance": 0.08,
        }
    ]
    find_similar = AsyncMock(return_value=candidates)

    result = await detector.check_duplicates(
        issue_id=1,
        project_name="snapcraft",
        title="Build fails",
        summary="Build failure",
        embedding=[0.1, 0.2, 0.3],
        find_similar_fn=find_similar,
    )

    assert "duplicate_of_issue_id" not in (result or {})


@pytest.mark.asyncio
async def test_check_duplicates_handles_llm_error_gracefully():
    detector, _emb, llm_client = _make_detector()
    llm_client.complete.side_effect = Exception("LLM timeout")
    candidates = [
        {
            "issue_id": 2,
            "external_id": "42",
            "project_name": "snapcraft",
            "title": "Issue",
            "summary": "Summary",
            "distance": 0.05,
        }
    ]
    find_similar = AsyncMock(return_value=candidates)

    result = await detector.check_duplicates(
        issue_id=1,
        project_name="snapcraft",
        title="Build fails",
        summary="Build failure",
        embedding=[0.1, 0.2, 0.3],
        find_similar_fn=find_similar,
    )

    assert result == {"candidates_compared": 1}


@pytest.mark.asyncio
async def test_rewrite_summary():
    detector, _emb, llm_client = _make_detector()
    llm_client.complete.return_value = _make_response(
        "May duplicate of snapcraft#42. Build failure on core24."
    )

    result = await detector.rewrite_summary(
        original_summary="Build failure on core24",
        duplicate_refs=["snapcraft#42"],
    )

    assert result == "May duplicate of snapcraft#42. Build failure on core24."


def test_parse_json_strips_think_tags():
    content = '<think>reasoning here</think>\n{"is_duplicate": true, "confidence": 80, "reason": "same"}'
    result = _parse_json_response(content)
    assert result == {"is_duplicate": True, "confidence": 80, "reason": "same"}


def test_parse_json_returns_none_on_invalid():
    result = _parse_json_response("this is not json at all")
    assert result is None
