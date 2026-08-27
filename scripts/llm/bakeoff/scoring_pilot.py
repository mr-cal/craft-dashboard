"""Run the Phase 5 scoring bake-off through the full tool-calling loop."""

from __future__ import annotations

import asyncio
import inspect
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from craft_dashboard.config import load_config
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.git_mirrors.paths import (
    canonical_git_project_name,
    mirror_path_for,
    resolve_allowed_projects,
)
from craft_dashboard.llm.tool_dispatch import ToolContext, dispatch_tool_call
from craft_dashboard.llm.tools import TOOL_SCHEMAS
from craft_dashboard.models.project import Project
from craft_dashboard.repositories.issue_repository import IssueRepository
from craft_dashboard.settings import Settings
from sqlalchemy import select

from scripts.llm.bakeoff.common import (
    BakeoffResult,
    estimate_cost_usd,
    fetch_issue,
    load_old_scores,
    load_sample,
    make_client,
    parse_scoring_output,
)
from scripts.llm.bakeoff.prompt_lab import build_round1_baseline, build_scoring_messages

if TYPE_CHECKING:
    from craft_dashboard.llm.client import LLMClient
    from craft_dashboard.models.issue import Issue
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_TOKEN_CEILING = 120_000
DEFAULT_TOOL_TIMEOUT_S = 20.0
DEFAULT_SWEEP_CAPS = (3, 4, 6, 8)


async def _allowed_projects(
    session: AsyncSession, settings: Settings
) -> dict[str, str]:
    config = load_config(settings.config_path)
    result = await session.execute(select(Project.name, Project.github_org))
    project_orgs = dict(result.all())
    return resolve_allowed_projects(
        craft_projects=config.craft_projects,
        project_orgs=project_orgs,
    )


def _pinned_sha_for_project(
    project: str, mirror_dir: Path, allowed_projects: dict[str, str]
) -> str:
    mirror = mirror_path_for(
        project, mirror_dir=mirror_dir, allowed_projects=allowed_projects
    )
    proc = subprocess.run(
        ["git", f"--git-dir={mirror}", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _load_pinned_shas(
    *, mirror_dir: Path, allowed_projects: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Load pinned SHAs once per run.

    Mirrors that fail to resolve are recorded in ``pin_errors`` so the current
    issue can fail in isolation while the tool context only exposes repos whose
    mirrors resolved successfully.
    """
    pinned_shas: dict[str, str] = {}
    pin_errors: dict[str, str] = {}
    for project in allowed_projects:
        try:
            pinned_shas[project] = _pinned_sha_for_project(
                project, mirror_dir, allowed_projects
            )
        except Exception as exc:
            pin_errors[project] = str(exc)
    return pinned_shas, pin_errors


async def _load_related_baseline(
    session: AsyncSession, *, issue_id: int, settings: Settings
) -> list[dict[str, object]]:
    repo = IssueRepository(session)
    related = await repo.find_similar_issues(
        issue_id=issue_id,
        top_n=settings.related_issues_top_n,
        similarity_threshold=settings.related_issues_similarity_threshold,
    )
    return [
        {
            **item,
            "ref": f"{item['project_name']}#{item['external_id']}",
            "confidence": int(round(float(item.get("similarity", 0.0)) * 100)),
        }
        for item in related
    ]


def _raise_pin_error(project: str, pin_errors: dict[str, str]) -> None:
    canonical = canonical_git_project_name(project)
    if canonical in pin_errors:
        raise RuntimeError(pin_errors[canonical])


async def _maybe_close(client: LLMClient) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _ensure_dir(path: Path) -> None:
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)


async def _write_text(path: Path, content: str) -> None:
    await asyncio.to_thread(path.write_text, content)


def _transcript_path(transcripts_dir: Path, *, model: str, issue_ref: str) -> Path:
    safe_model = model.replace("/", "__")
    safe_issue = issue_ref.replace("#", "_")
    return transcripts_dir / f"{safe_model}__{safe_issue}.json"


async def _dispatch_with_timeout(
    tool_ctx: ToolContext, *, name: str, arguments: dict[str, object]
) -> str:
    try:
        return await asyncio.wait_for(
            dispatch_tool_call(tool_ctx, name=name, arguments=arguments),
            timeout=DEFAULT_TOOL_TIMEOUT_S,
        )
    except TimeoutError:
        return f"Error: tool call timed out after {DEFAULT_TOOL_TIMEOUT_S:.1f}s"


async def _run_one(
    *,
    client: LLMClient,
    model: str,
    backend: str,
    issue: Issue,
    issue_ref: str,
    project: str,
    tool_ctx: ToolContext,
    baseline: str,
    max_rounds: int,
    token_ceiling: int,
) -> tuple[BakeoffResult, list[dict[str, object]]]:
    result = BakeoffResult(issue_ref=issue_ref, model=model, backend=backend)
    messages: list[dict[str, Any]] = build_scoring_messages(
        title=issue.title,
        body=issue.body,
        issue_type=issue.issue_type,
        labels=issue.labels or [],
        project=project,
        baseline=baseline,
    )
    transcript: list[dict[str, object]] = []
    start = time.monotonic()
    try:
        for round_num in range(1, max_rounds + 1):
            # Force at least one grounding tool call before the model may
            # finalize: with tool_choice="auto" alone, budget/challenger
            # models observed in the real bake-off overwhelmingly skipped
            # tools entirely and answered directly from the baseline bundle,
            # producing scores/related_work with no verifiable evidence
            # (judge-graded ~0% pass rate). Requiring one tool call in round
            # 1 only (subsequent rounds stay "auto" so the model can still
            # choose to finalize once it has enough evidence) is a minimal,
            # low-risk way to stop that degenerate no-evidence path.
            tool_choice = "required" if round_num == 1 else "auto"
            response = await client.complete(
                model=model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice=tool_choice,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
            result.rounds_used = round_num
            result.prompt_tokens += response.prompt_tokens
            result.completion_tokens += response.completion_tokens
            call_cost = estimate_cost_usd(response, model)
            if call_cost is not None:
                result.cost_usd = (result.cost_usd or 0.0) + call_cost
            transcript.append(
                {
                    "round": round_num,
                    "content": response.content,
                    "tool_calls": response.tool_calls,
                    "cost_usd": call_cost,
                }
            )

            if not response.tool_calls:
                parsed = parse_scoring_output(response.content)
                if parsed is None:
                    result.error = "unparsable final output"
                else:
                    result.new_output = dict(parsed)
                    related = parsed.get("related_work")
                    if isinstance(related, list):
                        result.related_work = related
                    result.completed = True
                break

            if result.prompt_tokens + result.completion_tokens > token_ceiling:
                result.error = f"token ceiling exceeded ({token_ceiling})"
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": response.tool_calls,
                }
            )
            for call in response.tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name", ""))
                raw_arguments = function.get("arguments") or "{}"
                arguments = json.loads(raw_arguments)
                result.tools_called.append(name)
                tool_text = await _dispatch_with_timeout(
                    tool_ctx,
                    name=name,
                    arguments=arguments,
                )
                transcript.append(
                    {
                        "round": round_num,
                        "tool_name": name,
                        "tool_arguments": arguments,
                        "tool_output": tool_text,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": tool_text,
                    }
                )
        else:
            result.error = f"max rounds reached ({max_rounds})"
    except Exception as exc:
        result.error = str(exc)
    finally:
        result.wall_seconds = time.monotonic() - start
    return result, transcript


async def run_scoring_pilot(
    *,
    session: AsyncSession,
    models: list[str],
    backend: str,
    sample_path: Path,
    transcripts_dir: Path,
    mirror_dir: Path,
    api_key: str = "",
    base_url: str = "",
    ca_cert: str = "",
    max_rounds: int = 6,
    token_ceiling: int = DEFAULT_TOKEN_CEILING,
    max_spend_usd: float | None = None,
    eval_server_base_url: str = "",
    eval_api_token: str = "",
) -> list[BakeoffResult]:
    """Run the scoring bake-off for every selected model and issue."""
    await _ensure_dir(transcripts_dir)
    sample = await asyncio.to_thread(load_sample, sample_path)
    settings = Settings()
    allowed_projects = await _allowed_projects(session, settings)
    pinned_shas, pin_errors = await asyncio.to_thread(
        _load_pinned_shas,
        mirror_dir=mirror_dir,
        allowed_projects=allowed_projects,
    )
    usable_allowed_projects = {
        project: org
        for project, org in allowed_projects.items()
        if project in pinned_shas
    }
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
                    result = BakeoffResult(
                        issue_ref=issue_ref,
                        model=model,
                        backend=backend,
                        error="issue not found",
                    )
                    transcript: list[dict[str, object]] = []
                else:
                    try:
                        _raise_pin_error(entry["project"], pin_errors)
                        related = await _load_related_baseline(
                            session, issue_id=issue.id, settings=settings
                        )
                        baseline = build_round1_baseline(
                            project=entry["project"],
                            body=issue.body,
                            mirror_dir=mirror_dir,
                            allowed_projects=usable_allowed_projects,
                            related=related,
                        )
                        tool_ctx = ToolContext(
                            mirror_dir=mirror_dir,
                            allowed_projects=usable_allowed_projects,
                            pinned_shas=pinned_shas,
                            eval_server_base_url=eval_server_base_url,
                            eval_api_token=eval_api_token,
                            issue_id=issue.id,
                        )
                        result, transcript = await _run_one(
                            client=client,
                            model=model,
                            backend=backend,
                            issue=issue,
                            issue_ref=issue_ref,
                            project=entry["project"],
                            tool_ctx=tool_ctx,
                            baseline=baseline,
                            max_rounds=max_rounds,
                            token_ceiling=token_ceiling,
                        )
                    except Exception as exc:
                        result = BakeoffResult(
                            issue_ref=issue_ref,
                            model=model,
                            backend=backend,
                            error=str(exc),
                        )
                        transcript = []
                if issue is not None:
                    result.old_scores = await load_old_scores(session, issue.id)
                results.append(result)

                if result.cost_usd is not None:
                    cumulative_cost += result.cost_usd
                    if max_spend_usd is not None and cumulative_cost > max_spend_usd:
                        raise RuntimeError(
                            f"max spend exceeded: {cumulative_cost:.4f} > {max_spend_usd:.4f}"
                        )

                await _write_text(
                    _transcript_path(
                        transcripts_dir,
                        model=model,
                        issue_ref=issue_ref,
                    ),
                    json.dumps(
                        {
                            "issue_ref": issue_ref,
                            "model": model,
                            "backend": backend,
                            "rounds_used": result.rounds_used,
                            "completed": result.completed,
                            "tools_called": result.tools_called,
                            "final_output": result.new_output,
                            "transcript": transcript,
                        },
                        indent=2,
                    )
                    + "\n",
                )
        finally:
            await _maybe_close(client)
    return results


def _score_map(results: list[BakeoffResult]) -> dict[str, dict[str, Any]]:
    return {
        result.issue_ref: result.new_output.get("scores", {})
        for result in results
        if result.completed
    }


async def run_max_rounds_sweep(
    *,
    session: AsyncSession,
    model: str,
    backend: str,
    sample_path: Path,
    transcripts_dir: Path,
    mirror_dir: Path,
    api_key: str = "",
    base_url: str = "",
    ca_cert: str = "",
    caps: tuple[int, ...] = DEFAULT_SWEEP_CAPS,
    max_spend_usd: float | None = None,
    eval_server_base_url: str = "",
    eval_api_token: str = "",
    initial_spend_usd: float = 0.0,
    skip_caps: frozenset[int] = frozenset(),
) -> list[dict[str, float | int]]:
    """Run the same sample across several max-round caps.

    When ``max_spend_usd`` is set, the remaining budget is carried forward to
    each successive cap so the sweep is bounded cumulatively, not per-cap.
    ``initial_spend_usd`` seeds the cumulative total with cost already spent
    by a prior run (e.g. the mandatory base run) against the same budget, so
    ``--max-spend-usd`` bounds the whole CLI invocation, not each call
    independently. ``skip_caps`` lets the caller omit a cap already covered
    by that prior run instead of re-running (and re-paying for) it here.
    """
    sweep_results: list[dict[str, float | int]] = []
    previous_scores: dict[str, dict[str, Any]] | None = None
    cumulative_cost = initial_spend_usd
    for cap in caps:
        if cap in skip_caps:
            continue
        remaining_budget: float | None = None
        if max_spend_usd is not None:
            remaining_budget = max(max_spend_usd - cumulative_cost, 0.0)
            if remaining_budget <= 0:
                msg = f"max spend exceeded: {cumulative_cost:.4f} > {max_spend_usd:.4f}"
                raise RuntimeError(msg)
        results = await run_scoring_pilot(
            session=session,
            models=[model],
            backend=backend,
            sample_path=sample_path,
            transcripts_dir=transcripts_dir / f"max-rounds-{cap}",
            mirror_dir=mirror_dir,
            api_key=api_key,
            base_url=base_url,
            ca_cert=ca_cert,
            max_rounds=cap,
            max_spend_usd=remaining_budget,
            eval_server_base_url=eval_server_base_url,
            eval_api_token=eval_api_token,
        )
        cumulative_cost += sum(result.cost_usd or 0.0 for result in results)
        current_scores = _score_map(results)
        if previous_scores is None:
            score_change_fraction = 0.0
        else:
            compared = 0
            changed = 0
            for issue_ref, scores in current_scores.items():
                if issue_ref not in previous_scores:
                    continue
                compared += 1
                if previous_scores[issue_ref] != scores:
                    changed += 1
            score_change_fraction = changed / compared if compared else 0.0
        sweep_results.append(
            {
                "cap": cap,
                "completion_rate": (
                    sum(1 for result in results if result.completed) / len(results)
                    if results
                    else 0.0
                ),
                "mean_cost_usd": statistics.mean(
                    [result.cost_usd or 0.0 for result in results]
                )
                if results
                else 0.0,
                "mean_wall_seconds": statistics.mean(
                    [result.wall_seconds for result in results]
                )
                if results
                else 0.0,
                "score_change_fraction": score_change_fraction,
            }
        )
        previous_scores = current_scores
    return sweep_results


@click.command()
@click.option(
    "--model", "models", multiple=True, required=True, help="Candidate model(s)."
)
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
    default=Path(__file__).parent / "scoring_sample.json",
    show_default=True,
)
@click.option("--transcripts-dir", type=click.Path(path_type=Path), required=True)
@click.option("--mirror-dir", type=click.Path(path_type=Path), required=True)
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True)
@click.option("--max-rounds", default=6, show_default=True, type=int)
@click.option("--max-spend-usd", default=5.0, show_default=True, type=float)
@click.option(
    "--eval-server-base-url",
    required=True,
    help="Base URL for /api/eval/* helper endpoints used by related_issues and issue_detail tools.",
)
@click.option(
    "--eval-api-token",
    required=True,
    help="Bearer token for the eval helper endpoints used during tool dispatch.",
)
@click.option("--sweep/--no-sweep", default=False, show_default=True)
def cli(
    models: tuple[str, ...],
    backend: str,
    sample_path: Path,
    transcripts_dir: Path,
    mirror_dir: Path,
    out_path: Path,
    max_rounds: int,
    max_spend_usd: float,
    eval_server_base_url: str,
    eval_api_token: str,
    sweep: bool,
) -> None:
    """Run the scoring bake-off and write its Markdown report."""
    from scripts.llm.bakeoff.report import write_scoring_report

    settings = Settings()
    if sweep and len(models) != 1:
        msg = "--sweep requires exactly one --model"
        raise click.UsageError(msg)

    async def _main() -> tuple[list[BakeoffResult], list[dict[str, float | int]]]:
        engine = get_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
        try:
            session_factory = get_session_factory(engine)
            async with session_factory() as session:
                results = await run_scoring_pilot(
                    session=session,
                    models=list(models),
                    backend=backend,
                    sample_path=sample_path,
                    transcripts_dir=transcripts_dir,
                    mirror_dir=mirror_dir,
                    api_key=settings.openrouter_api_key,
                    base_url="",
                    max_rounds=max_rounds,
                    max_spend_usd=max_spend_usd,
                    eval_server_base_url=eval_server_base_url,
                    eval_api_token=eval_api_token,
                )
                sweep_results: list[dict[str, float | int]] = []
                if sweep:
                    # The base run above already covers cap == max_rounds, so
                    # skip re-running (and re-paying for) it here, and carry
                    # its spend forward so --max-spend-usd bounds the whole
                    # invocation rather than the base run and sweep
                    # independently.
                    base_spend = sum(result.cost_usd or 0.0 for result in results)
                    sweep_caps = tuple(sorted({*DEFAULT_SWEEP_CAPS, max_rounds}))
                    sweep_results = await run_max_rounds_sweep(
                        session=session,
                        model=models[0],
                        backend=backend,
                        sample_path=sample_path,
                        transcripts_dir=transcripts_dir,
                        mirror_dir=mirror_dir,
                        api_key=settings.openrouter_api_key,
                        caps=sweep_caps,
                        max_spend_usd=max_spend_usd,
                        eval_server_base_url=eval_server_base_url,
                        eval_api_token=eval_api_token,
                        initial_spend_usd=base_spend,
                        skip_caps=frozenset({max_rounds}),
                    )
                return results, sweep_results
        finally:
            await engine.dispose()

    results, sweep_results = asyncio.run(_main())
    write_scoring_report(
        results,
        out_path,
        max_rounds=max_rounds,
        sweep_results=sweep_results,
    )


if __name__ == "__main__":
    cli()
