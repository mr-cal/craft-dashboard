"""Tests for craft_dashboard.llm.baseline."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from craft_dashboard.llm.baseline import (
    BaselineError,
    _extract_candidate_patterns,
    build_round1_baseline,
)
from craft_dashboard.llm.tool_dispatch import ToolContext

TEST_EVAL_API_TOKEN = "test-token"


@contextmanager
def patch_repo_layout(layout: dict[str, int]):
    with patch(
        "craft_dashboard.llm.baseline.reader.repo_layout",
        new=AsyncMock(return_value=layout),
    ) as mocked:
        yield mocked


@contextmanager
def patch_grep_repo(hits: list[str]):
    with patch(
        "craft_dashboard.llm.baseline.reader.grep_repo",
        new=AsyncMock(return_value=hits),
    ) as mocked:
        yield mocked


@contextmanager
def patch_related_issues(candidates: list[dict]):
    payload = {"results": candidates}
    with patch(
        "craft_dashboard.llm.baseline._dispatch_http_tool",
        new=AsyncMock(return_value=str(payload).replace("'", '"')),
    ) as mocked:
        yield mocked


def _tool_ctx() -> ToolContext:
    return ToolContext(
        mirror_dir=Path.cwd(),
        allowed_projects={"craft-parts": "canonical"},
        pinned_shas={"craft-parts": "a" * 40},
        eval_server_base_url="http://testserver",
        eval_api_token=TEST_EVAL_API_TOKEN,
        issue_id=1,
    )


class TestExtractCandidatePatterns:
    """Tests for _extract_candidate_patterns."""

    def test_extracts_backtick_identifiers(self) -> None:
        text = "Calling `parse_manifest()` raises `KeyError` unexpectedly."
        patterns = _extract_candidate_patterns(text)
        assert "parse_manifest" in patterns
        assert "KeyError" in patterns

    def test_extracts_snake_case_and_camel_case_words(self) -> None:
        text = "The buildEnvironment step fails inside collect_lifecycle_steps."
        patterns = _extract_candidate_patterns(text)
        assert "buildEnvironment" in patterns
        assert "collect_lifecycle_steps" in patterns

    def test_ignores_short_common_words(self) -> None:
        text = "This is a bug in the app when it runs."
        patterns = _extract_candidate_patterns(text)
        assert patterns == []

    def test_caps_at_ten_patterns(self) -> None:
        text = " ".join(f"identifier_number_{i}" for i in range(20))
        patterns = _extract_candidate_patterns(text)
        assert len(patterns) <= 10


class TestBuildRound1Baseline:
    """Tests for build_round1_baseline."""

    @pytest.mark.asyncio
    async def test_includes_repo_layout(self) -> None:
        with (
            patch_repo_layout({"craft_parts": 12}),
            patch_grep_repo([]),
            patch_related_issues([]),
        ):
            baseline = await build_round1_baseline(
                _tool_ctx(),
                project="craft-parts",
                title="parse_manifest raises KeyError",
                body=None,
            )
        assert "craft_parts\t12 files" in baseline

    @pytest.mark.asyncio
    async def test_discards_zero_hit_patterns(self) -> None:
        with (
            patch_repo_layout({}),
            patch_grep_repo([]),
            patch_related_issues([]),
        ):
            baseline = await build_round1_baseline(
                _tool_ctx(),
                project="craft-parts",
                title="parse_manifest raises KeyError",
                body=None,
            )
        assert "parse_manifest" not in baseline

    @pytest.mark.asyncio
    async def test_discards_over_100_hit_patterns(self) -> None:
        with (
            patch_repo_layout({}),
            patch_grep_repo([f"craft-parts: hit {i}" for i in range(150)]),
            patch_related_issues([]),
        ):
            baseline = await build_round1_baseline(
                _tool_ctx(),
                project="craft-parts",
                title="parse_manifest raises KeyError",
                body=None,
            )
        assert "parse_manifest" not in baseline

    @pytest.mark.asyncio
    async def test_layout_failure_after_preflight_raises(self) -> None:
        with (
            patch(
                "craft_dashboard.llm.baseline.reader.repo_layout",
                new=AsyncMock(side_effect=RuntimeError("mirror gone")),
            ),
            patch_grep_repo([]),
            patch_related_issues([]),
        ):
            with pytest.raises(BaselineError):
                await build_round1_baseline(
                    _tool_ctx(),
                    project="craft-parts",
                    title="parse_manifest raises KeyError",
                    body=None,
                )
