"""Pricing data and cost estimation for OpenRouter-backed LLM evaluation.

Prices are sourced from OpenRouter's public ``/api/v1/models`` endpoint and
are USD per single token (OpenRouter itself publishes per-million-token
rates). This list only needs to cover models actually used for server-side
evaluation -- update it if ``OPENROUTER_MODEL``/``--model`` changes to
something not listed here. Unknown models simply produce no cost estimate
rather than an error, so evaluation is never blocked on pricing data.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Per-token USD pricing for one model."""

    prompt_per_token: float
    completion_per_token: float


def _per_million(prompt_usd: float, completion_usd: float) -> ModelPricing:
    return ModelPricing(
        prompt_per_token=prompt_usd / 1_000_000,
        completion_per_token=completion_usd / 1_000_000,
    )


# Rates current as of the pricing lookups done when each model was added;
# re-check https://openrouter.ai/api/v1/models if costs reported here look
# off from what OpenRouter's dashboard shows.
KNOWN_MODEL_PRICING: dict[str, ModelPricing] = {
    "google/gemini-2.5-flash-lite": _per_million(0.10, 0.40),
    "qwen/qwen3.6-35b-a3b": _per_million(0.14, 1.00),
    "openai/gpt-4o-mini": _per_million(0.15, 0.60),
    "anthropic/claude-3.5-haiku": _per_million(0.80, 4.00),
}


def estimate_cost_usd(
    model: str, *, prompt_tokens: int, completion_tokens: int
) -> float | None:
    """Estimate the USD cost of one completion, or None if unpriced.

    Returns ``None`` (rather than 0) when *model* isn't in
    :data:`KNOWN_MODEL_PRICING`, so callers can distinguish "free/local" from
    "cost unknown" and avoid reporting a misleadingly precise total.
    """
    pricing = KNOWN_MODEL_PRICING.get(model)
    if pricing is None:
        return None
    return (
        prompt_tokens * pricing.prompt_per_token
        + completion_tokens * pricing.completion_per_token
    )


def format_usd(amount: float) -> str:
    """Format a USD amount for display (e.g. "$0.0042", "$12.30")."""
    if amount < 1:
        return f"${amount:.4f}"
    return f"${amount:,.2f}"
