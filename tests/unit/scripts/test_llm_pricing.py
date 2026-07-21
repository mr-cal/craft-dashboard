"""Tests for scripts.llm.pricing."""

from __future__ import annotations

from scripts.llm.pricing import estimate_cost_usd, format_usd


class TestEstimateCostUsd:
    """Tests for estimate_cost_usd()."""

    def test_computes_cost_for_known_model(self) -> None:
        cost = estimate_cost_usd(
            "google/gemini-2.5-flash-lite",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        assert cost == 0.10 + 0.40

    def test_returns_none_for_unknown_model(self) -> None:
        assert (
            estimate_cost_usd(
                "some/unknown-model", prompt_tokens=100, completion_tokens=50
            )
            is None
        )

    def test_zero_tokens_costs_zero(self) -> None:
        cost = estimate_cost_usd(
            "google/gemini-2.5-flash-lite", prompt_tokens=0, completion_tokens=0
        )
        assert cost == 0.0

    def test_prompt_and_completion_priced_independently(self) -> None:
        prompt_only = estimate_cost_usd(
            "qwen/qwen3.6-35b-a3b", prompt_tokens=1_000_000, completion_tokens=0
        )
        completion_only = estimate_cost_usd(
            "qwen/qwen3.6-35b-a3b", prompt_tokens=0, completion_tokens=1_000_000
        )
        assert prompt_only == 0.14
        assert completion_only == 1.00


class TestFormatUsd:
    """Tests for format_usd()."""

    def test_formats_sub_dollar_amounts_with_four_decimals(self) -> None:
        assert format_usd(0.0042) == "$0.0042"

    def test_formats_amounts_over_a_dollar_with_two_decimals(self) -> None:
        assert format_usd(12.3) == "$12.30"

    def test_formats_large_amounts_with_thousands_separator(self) -> None:
        assert format_usd(1234.5) == "$1,234.50"

    def test_formats_zero(self) -> None:
        assert format_usd(0.0) == "$0.0000"
