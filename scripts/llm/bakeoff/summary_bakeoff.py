"""Run the Phase 5 closed-item summary bake-off."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import click
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.settings import Settings

from scripts.llm.bakeoff.common import (
    BakeoffResult,
    build_summary_messages,
    estimate_cost_usd,
    fetch_issue,
    load_sample,
    make_client,
    parse_scoring_output,
)

if TYPE_CHECKING:
    from craft_dashboard.llm.client import LLMClient
    from sqlalchemy.ext.asyncio import AsyncSession

CANDIDATES = [
    "z-ai/glm-5.2",
    "qwen/qwen3.8-27b",
    "deepseek/deepseek-v4-pro-0813",
    "qwen/qwen3.6-35b-a3b",
]


async def _maybe_close(client: LLMClient) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def run_summary_bakeoff(
    *,
    session: AsyncSession,
    models: list[str],
    backend: str,
    sample_path: Path,
    api_key: str = "",
    base_url: str = "",
    ca_cert: str = "",
    max_spend_usd: float | None = None,
) -> list[BakeoffResult]:
    """Run the closed-item summary bake-off across the frozen sample.

    ``max_spend_usd`` bounds the cumulative estimated cost across every
    (model, issue) call in this run; once exceeded, the run stops and raises
    a ``RuntimeError`` rather than continuing to spend past budget.
    """
    sample = await asyncio.to_thread(load_sample, sample_path)
    results: list[BakeoffResult] = []
    cumulative_cost = 0.0

    for model in models:
        client = make_client(
            backend=backend,
            api_key=api_key,
            base_url=base_url,
            ca_cert=ca_cert,
        )
        try:
            for entry in sample:
                issue_ref = f"{entry['project']}#{entry['external_id']}"
                issue = await fetch_issue(
                    session,
                    source=entry["source"],
                    project_name=entry["project"],
                    external_id=entry["external_id"],
                )
                if issue is None:
                    results.append(
                        BakeoffResult(
                            issue_ref=issue_ref,
                            model=model,
                            backend=backend,
                            error="issue not found",
                        )
                    )
                    continue
                result = BakeoffResult(
                    issue_ref=issue_ref, model=model, backend=backend
                )
                try:
                    response = await client.complete(
                        model=model,
                        messages=build_summary_messages(issue),
                        max_tokens=4096,
                        response_format={"type": "json_object"},
                    )
                    result.rounds_used = 1
                    result.prompt_tokens = response.prompt_tokens
                    result.completion_tokens = response.completion_tokens
                    result.cost_usd = estimate_cost_usd(response, model)
                    parsed = parse_scoring_output(response.content)
                    if parsed is None or "summary" not in parsed:
                        result.error = "unparsable summary output"
                    else:
                        result.new_output = {"summary": parsed["summary"]}
                        result.completed = True
                except Exception as exc:
                    result.error = str(exc)
                results.append(result)
                if result.cost_usd is not None:
                    cumulative_cost += result.cost_usd
                    if max_spend_usd is not None and cumulative_cost > max_spend_usd:
                        msg = (
                            f"max spend exceeded: {cumulative_cost:.4f} > "
                            f"{max_spend_usd:.4f}"
                        )
                        raise RuntimeError(msg)
        finally:
            await _maybe_close(client)
    return results


@click.command()
@click.option("--model", "models", multiple=True, default=CANDIDATES)
@click.option(
    "--backend",
    type=click.Choice(["openrouter", "local"]),
    default="openrouter",
    show_default=True,
)
@click.option(
    "--sample",
    "sample_path",
    type=click.Path(exists=True, path_type=Path),
    default=Path(__file__).parent / "summary_sample.json",
    show_default=True,
)
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True)
@click.option("--max-spend-usd", default=5.0, show_default=True, type=float)
def cli(
    models: tuple[str, ...],
    backend: str,
    sample_path: Path,
    out_path: Path,
    max_spend_usd: float,
) -> None:
    """Run the summary bake-off and write its Markdown report."""
    from scripts.llm.bakeoff.report import write_summary_report

    settings = Settings()

    async def _main() -> list[BakeoffResult]:
        engine = get_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
        try:
            session_factory = get_session_factory(engine)
            async with session_factory() as session:
                return await run_summary_bakeoff(
                    session=session,
                    models=list(models),
                    backend=backend,
                    sample_path=sample_path,
                    api_key=settings.openrouter_api_key,
                    max_spend_usd=max_spend_usd,
                )
        finally:
            await engine.dispose()

    results = asyncio.run(_main())
    write_summary_report(results, [], out_path)


if __name__ == "__main__":
    cli()
