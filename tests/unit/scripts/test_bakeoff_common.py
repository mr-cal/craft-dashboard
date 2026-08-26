"""Unit tests for scripts.llm.bakeoff.common."""

from __future__ import annotations

import pytest
from craft_dashboard.llm.client import LLMResponse
from scripts.llm.bakeoff.common import (
    BakeoffResult,
    estimate_cost_usd,
    load_sample,
    make_client,
    parse_scoring_output,
)


class TestLoadSample:
    def test_loads_source_project_external_id(self, tmp_path) -> None:
        p = tmp_path / "sample.json"
        p.write_text(
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
        )
        assert load_sample(p) == [
            {"source": "github", "project": "craft-parts", "external_id": "1"}
        ]

    def test_raises_on_empty(self, tmp_path) -> None:
        p = tmp_path / "s.json"
        p.write_text("[]")
        with pytest.raises(ValueError, match="empty"):
            load_sample(p)

    def test_raises_when_source_missing(self, tmp_path) -> None:
        p = tmp_path / "s.json"
        p.write_text('[{"project": "craft-parts", "external_id": "1"}]')
        with pytest.raises(ValueError, match="source"):
            load_sample(p)


class TestEstimateCost:
    def test_prefers_backend_reported_cost(self) -> None:
        resp = LLMResponse(
            content="",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            model="z-ai/glm-5.2",
            cost_usd=0.0042,
        )
        assert estimate_cost_usd(resp, "z-ai/glm-5.2") == 0.0042

    def test_falls_back_to_static_table_for_local_none(self) -> None:
        resp = LLMResponse(
            content="",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            total_tokens=2_000_000,
            model="qwen/qwen3.8-27b",
            cost_usd=None,
        )
        assert estimate_cost_usd(resp, "qwen/qwen3.8-27b") == pytest.approx(
            0.425 + 2.55
        )

    def test_returns_none_for_unknown_model_without_cost(self) -> None:
        resp = LLMResponse(
            content="",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            model="mystery",
            cost_usd=None,
        )
        assert estimate_cost_usd(resp, "mystery") is None


class TestParseScoringOutput:
    def test_reuses_production_parser_and_strips_think_blocks(self) -> None:
        content = '<think>reasoning…</think>\n{"scores": {"impact": 70}}'
        assert parse_scoring_output(content) == {"scores": {"impact": 70}}

    def test_unparsable_returns_none(self) -> None:
        assert parse_scoring_output("not json at all") is None


class TestMakeClient:
    def test_openrouter_backend(self) -> None:
        client = make_client(backend="openrouter", api_key="k")
        assert client.__class__.__name__ == "OpenRouterClient"

    def test_local_backend(self) -> None:
        client = make_client(backend="local", base_url="http://x/v1")
        assert client.__class__.__name__ == "LocalLLMClient"


def test_bakeoff_result_defaults() -> None:
    result = BakeoffResult(issue_ref="craft-parts#1", model="m", backend="b")
    assert result.old_scores == {}
