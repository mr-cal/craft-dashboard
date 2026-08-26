"""Markdown reporting for Phase 5 bake-offs."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from scripts.llm.bakeoff.common import BakeoffResult

SUMMARY_MARGIN = 15
INCUMBENT_MODEL = "qwen/qwen3.6-35b-a3b"


def _costs(results: Iterable[BakeoffResult]) -> list[float]:
    return [result.cost_usd for result in results if result.cost_usd is not None]


def extrapolate_backfill(
    results: list[BakeoffResult], *, item_count: int
) -> dict[str, float | int | str]:
    """Project backfill cost with variance-aware statistics."""
    costs = _costs(results)
    if not costs:
        return {
            "item_count": item_count,
            "sample_size": 0,
            "mean_cost_per_item": 0.0,
            "median_cost_per_item": 0.0,
            "stdev_cost_per_item": 0.0,
            "min_cost_per_item": 0.0,
            "max_cost_per_item": 0.0,
            "projected_total": 0.0,
            "caveats": "No measured cost rows were available.",
        }

    mean_cost = statistics.mean(costs)
    return {
        "item_count": item_count,
        "sample_size": len(costs),
        "mean_cost_per_item": mean_cost,
        "median_cost_per_item": statistics.median(costs),
        "stdev_cost_per_item": statistics.stdev(costs) if len(costs) > 1 else 0.0,
        "min_cost_per_item": min(costs),
        "max_cost_per_item": max(costs),
        "projected_total": mean_cost * item_count,
        "caveats": (
            "Point estimates are insufficient: review sample size, variance, "
            "and prompt-size skew before authorizing any backfill."
        ),
    }


def _group_by_model(results: Iterable[BakeoffResult]) -> dict[str, list[BakeoffResult]]:
    grouped: dict[str, list[BakeoffResult]] = defaultdict(list)
    for result in results:
        grouped[result.model].append(result)
    return dict(grouped)


def _group_by_issue(results: Iterable[BakeoffResult]) -> dict[str, list[BakeoffResult]]:
    grouped: dict[str, list[BakeoffResult]] = defaultdict(list)
    for result in results:
        grouped[result.issue_ref].append(result)
    return dict(grouped)


def _score_blob(data: dict[str, Any]) -> str:
    if not data:
        return "-"
    return ", ".join(f"{key}={value}" for key, value in sorted(data.items()))


def _related_blob(items: list[dict[str, Any]]) -> str:
    if not items:
        return "-"
    return "; ".join(
        f"{item.get('kind', '?')}:{item.get('ref', '?')} ({item.get('confidence', '?')})"
        for item in items
    )


def _summary_lines(results: list[BakeoffResult]) -> list[str]:
    lines = [
        "| Model | Items | Completion rate | Mean rounds | Mean cost_usd | Median cost_usd | Stdev cost_usd | Mean wall seconds |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model, rows in sorted(_group_by_model(results).items()):
        completed = [row for row in rows if row.completed]
        rounds = [row.rounds_used for row in rows]
        costs = _costs(rows)
        walls = [row.wall_seconds for row in rows]
        lines.append(
            f"| {model} | {len(rows)} | {len(completed) / len(rows) if rows else 0.0:.0%} | {statistics.mean(rounds) if rounds else 0.0:.2f} | {statistics.mean(costs) if costs else 0.0:.4f} | {statistics.median(costs) if costs else 0.0:.4f} | {statistics.stdev(costs) if len(costs) > 1 else 0.0:.4f} | {statistics.mean(walls) if walls else 0.0:.2f} |"
        )
    return lines


def _rounds_section(results: list[BakeoffResult], max_rounds: int) -> list[str]:
    counts = defaultdict(int)
    for result in results:
        counts[result.rounds_used] += 1
    lines = [
        "## MAX_TOOL_ROUNDS calibration",
        f"Evaluated against current cap = {max_rounds}.",
        "",
        "| Rounds used | Count |",
        "| --- | ---: |",
    ]
    for rounds_used, count in sorted(counts.items()):
        lines.append(f"| {rounds_used} | {count} |")
    lines.append("")
    lines.append(
        f"Current recommendation: keep MAX_TOOL_ROUNDS at {max_rounds} until sweep data is available."
    )
    return lines


def write_scoring_report(
    results: list[BakeoffResult], out_path: Path, *, max_rounds: int
) -> None:
    """Write the scoring bake-off Markdown report."""
    scoring_projection = extrapolate_backfill(results, item_count=2269)
    lines = [
        "# Phase 5 scoring bake-off",
        "",
        "## Per-model roll-up",
        *_summary_lines(results),
        "",
        "## Per-issue comparisons",
        "",
        "| Issue | Model | Old scores | New scores | Related work | Rounds | Tools | Prompt tokens | Completion tokens | Cost_usd | Wall seconds | Status |",
        "| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        status = (
            "completed" if result.completed else f"error: {result.error or 'unknown'}"
        )
        lines.append(
            "| {issue} | {model} | {old} | {new} | {related} | {rounds} | {tools} | {prompt} | {completion} | {cost:.4f} | {wall:.2f} | {status} |".format(
                issue=result.issue_ref,
                model=result.model,
                old=_score_blob(result.old_scores),
                new=_score_blob(result.new_output.get("scores", {})),
                related=_related_blob(result.related_work),
                rounds=result.rounds_used,
                tools=", ".join(result.tools_called) or "-",
                prompt=result.prompt_tokens,
                completion=result.completion_tokens,
                cost=result.cost_usd or 0.0,
                wall=result.wall_seconds,
                status=status,
            )
        )
    lines.extend(
        [
            "",
            "## Scoring backfill projection",
            "",
            f"- Sample size: {scoring_projection['sample_size']}",
            f"- Mean cost per item: {scoring_projection['mean_cost_per_item']:.4f}",
            f"- Median cost per item: {scoring_projection['median_cost_per_item']:.4f}",
            f"- Stdev cost per item: {scoring_projection['stdev_cost_per_item']:.4f}",
            f"- Min/Max cost per item: {scoring_projection['min_cost_per_item']:.4f} / {scoring_projection['max_cost_per_item']:.4f}",
            f"- Projected total for 2,269 items: {scoring_projection['projected_total']:.4f}",
            f"- Caveats: {scoring_projection['caveats']}",
            "",
            *_rounds_section(results, max_rounds),
            "",
        ]
    )
    out_path.write_text("\n".join(lines))


def write_summary_report(
    results: list[BakeoffResult], grades: list[dict[str, Any]], out_path: Path
) -> None:
    """Write the summary bake-off Markdown report."""
    grade_lookup = {
        (grade.get("issue_ref"), grade.get("model")): grade for grade in grades
    }
    model_grade_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for grade in grades:
        model_grade_map[str(grade.get("model"))].append(grade)

    incumbent_mean = (
        statistics.mean(
            [
                float(grade.get("faithfulness", 0.0))
                for grade in model_grade_map[INCUMBENT_MODEL]
            ]
        )
        if model_grade_map[INCUMBENT_MODEL]
        else 0.0
    )
    recommendation = f"keep incumbent ({INCUMBENT_MODEL})"
    best_model = INCUMBENT_MODEL
    best_mean = incumbent_mean
    for model, model_grades in model_grade_map.items():
        if model == INCUMBENT_MODEL:
            continue
        challenger_mean = statistics.mean(
            [float(grade.get("faithfulness", 0.0)) for grade in model_grades]
        )
        unique_failure = any(
            grade.get("grade") != "pass"
            and grade_lookup.get((grade.get("issue_ref"), INCUMBENT_MODEL), {}).get(
                "grade"
            )
            == "pass"
            for grade in model_grades
        )
        if (
            challenger_mean >= incumbent_mean + SUMMARY_MARGIN
            and not unique_failure
            and challenger_mean > best_mean
        ):
            best_model = model
            best_mean = challenger_mean
    if best_model != INCUMBENT_MODEL:
        recommendation = f"switch to {best_model}"

    projection = extrapolate_backfill(results, item_count=17206)
    lines = [
        "# Phase 5 summary bake-off",
        "",
        f"Decision rule: recommend switching only if a challenger beats the incumbent by at least {SUMMARY_MARGIN} mean faithfulness points and avoids any unique incumbent-safe failure.",
        "",
        f"Recommendation: {recommendation}",
        "",
        "## Mean faithfulness by model",
        "",
        "| Model | Mean faithfulness |",
        "| --- | ---: |",
    ]
    for model, model_grades in sorted(model_grade_map.items()):
        mean_faithfulness = statistics.mean(
            [float(grade.get("faithfulness", 0.0)) for grade in model_grades]
        )
        lines.append(f"| {model} | {mean_faithfulness:.2f} |")
    lines.extend(
        [
            "",
            "## Per-issue side-by-side summaries",
            "",
            "| Issue | Incumbent summary | Incumbent grade | GLM summary | GLM grade | Qwen 3.8 summary | Qwen 3.8 grade | DeepSeek summary | DeepSeek grade |",
            "| --- | --- | ---: | --- | ---: | --- | ---: | --- | ---: |",
        ]
    )
    by_issue = _group_by_issue(results)
    model_order = [
        INCUMBENT_MODEL,
        "z-ai/glm-5.2",
        "qwen/qwen3.8-27b",
        "deepseek/deepseek-v4-pro-0813",
    ]
    for issue_ref, issue_results in sorted(by_issue.items()):
        result_map = {result.model: result for result in issue_results}
        cells = [issue_ref]
        for model in model_order:
            result = result_map.get(model)
            grade = grade_lookup.get((issue_ref, model), {})
            summary = "-"
            if result is not None:
                summary = result.new_output.get("summary", result.error or "-")
            cells.extend(
                [summary.replace("\n", " "), str(grade.get("faithfulness", "-"))]
            )
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "## Summary backfill projection",
            "",
            f"- Sample size: {projection['sample_size']}",
            f"- Mean cost per item: {projection['mean_cost_per_item']:.4f}",
            f"- Median cost per item: {projection['median_cost_per_item']:.4f}",
            f"- Stdev cost per item: {projection['stdev_cost_per_item']:.4f}",
            f"- Projected total for 17,206 items: {projection['projected_total']:.4f}",
            f"- Caveats: {projection['caveats']}",
            "",
        ]
    )
    out_path.write_text("\n".join(lines))
