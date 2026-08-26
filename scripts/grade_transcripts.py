"""Judge-model grading for bake-off transcripts."""

from __future__ import annotations

import asyncio
import inspect
import json
import statistics
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from scripts.llm.bakeoff.common import make_client, parse_scoring_output

if TYPE_CHECKING:
    from craft_dashboard.llm.client import LLMClient


def load_transcripts(transcripts_dir: Path) -> list[dict[str, Any]]:
    """Load every stored transcript JSON file from a directory."""
    return [
        json.loads(path.read_text())
        for path in sorted(transcripts_dir.glob("*.json"))
        if path.is_file()
    ]


def _judge_messages(transcript: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You assess tool-use quality in deep-evaluation transcripts. "
                'Respond in JSON: {"grade": "pass|fail", "faithfulness": 0-100, '
                '"wasted_calls": <int>, "missed_evidence": [<text>], '
                '"premature": <bool>, "note": "<brief rationale>"}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(transcript, indent=2),
        },
    ]


async def _maybe_close(client: LLMClient) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def grade_transcripts(
    transcripts_dir: Path,
    *,
    judge_model: str,
    backend: str,
    api_key: str,
    base_url: str = "",
    ca_cert: str = "",
) -> list[dict[str, Any]]:
    """Grade each transcript with a judge model."""
    transcripts = await asyncio.to_thread(load_transcripts, transcripts_dir)
    client = make_client(
        backend=backend,
        api_key=api_key,
        base_url=base_url,
        ca_cert=ca_cert,
    )
    grades: list[dict[str, Any]] = []
    try:
        for transcript in transcripts:
            response = await client.complete(
                model=judge_model,
                messages=_judge_messages(transcript),
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
            parsed = parse_scoring_output(response.content) or {}
            grades.append(
                {
                    "issue_ref": transcript.get("issue_ref"),
                    "model": transcript.get("model"),
                    "rounds_used": transcript.get("rounds_used", 0),
                    **parsed,
                }
            )
    finally:
        await _maybe_close(client)
    return grades


def summarize_grades(grades: list[dict[str, Any]]) -> dict[str, float | int]:
    """Summarize judge findings across all transcripts."""
    total = len(grades)
    passes = sum(1 for grade in grades if grade.get("grade") == "pass")
    rounds = [float(grade.get("rounds_used", 0)) for grade in grades]
    return {
        "pass_rate": passes / total if total else 0.0,
        "average_rounds": statistics.mean(rounds) if rounds else 0.0,
        "wasted_call_flags": sum(
            1 for grade in grades if int(grade.get("wasted_calls", 0)) > 0
        ),
        "missed_evidence_flags": sum(
            1 for grade in grades if bool(grade.get("missed_evidence"))
        ),
        "premature_flags": sum(1 for grade in grades if bool(grade.get("premature"))),
    }


def write_grading_report(grades: list[dict[str, Any]], out_path: Path) -> None:
    """Write a Markdown report summarizing judge findings."""
    summary = summarize_grades(grades)
    lines = [
        "# Phase 5 transcript grading",
        "",
        f"- Pass rate: {summary['pass_rate']:.0%}",
        f"- Average rounds: {summary['average_rounds']:.2f}",
        f"- Wasted-call flags: {summary['wasted_call_flags']}",
        f"- Missed-evidence flags: {summary['missed_evidence_flags']}",
        f"- Premature flags: {summary['premature_flags']}",
        "",
        "| Issue | Model | Grade | Faithfulness | Wasted calls | Premature | Note |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    lines.extend(
        "| {issue} | {model} | {grade_label} | {faithfulness} | {wasted} | {premature} | {note} |".format(
            issue=grade.get("issue_ref", "-"),
            model=grade.get("model", "-"),
            grade_label=grade.get("grade", "-"),
            faithfulness=grade.get("faithfulness", "-"),
            wasted=grade.get("wasted_calls", 0),
            premature=grade.get("premature", False),
            note=str(grade.get("note", "-")).replace("\n", " "),
        )
        for grade in grades
    )
    out_path.write_text("\n".join(lines))


@click.command()
@click.option(
    "--transcripts-dir", type=click.Path(exists=True, path_type=Path), required=True
)
@click.option("--judge-model", required=True)
@click.option(
    "--backend",
    type=click.Choice(["openrouter", "local"]),
    default="openrouter",
    show_default=True,
)
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True)
@click.option("--api-key", default="", show_default=True)
@click.option("--base-url", default="", show_default=True)
def cli(
    transcripts_dir: Path,
    judge_model: str,
    backend: str,
    out_path: Path,
    api_key: str,
    base_url: str,
) -> None:
    """Grade transcript files and write a Markdown report."""

    async def _main() -> list[dict[str, Any]]:
        return await grade_transcripts(
            transcripts_dir,
            judge_model=judge_model,
            backend=backend,
            api_key=api_key,
            base_url=base_url,
        )

    grades = asyncio.run(_main())
    write_grading_report(grades, out_path)


if __name__ == "__main__":
    cli()
