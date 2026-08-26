"""Select reproducible, stratified bake-off samples.

Seed note: use ``--seed 41`` for the Phase 5 frozen fixtures.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import click
from craft_dashboard.database import get_engine, get_session_factory
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.llm_evaluation import LLMEvaluation
from craft_dashboard.models.project import Project
from craft_dashboard.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

DEFAULT_SEED = 41
_SHORT_BODY_MAX = 500
_MEDIUM_BODY_MAX = 4_000


def body_bucket(body_len: int) -> str:
    """Bucket body lengths for stratified selection."""
    if body_len < _SHORT_BODY_MAX:
        return "short"
    if body_len < _MEDIUM_BODY_MAX:
        return "medium"
    return "long"


def _stable_score(row: Mapping[str, object], *, seed: int) -> str:
    key = "|".join(
        [
            str(seed),
            str(row["category"]),
            str(row["name"]),
            str(row["source"]),
            str(row["external_id"]),
            body_bucket(int(row["body_len"])),
            "eval" if bool(row["has_eval"]) else "new",
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()


def choose_seeded_sample(
    rows: Iterable[Mapping[str, object]], *, count: int, seed: int
) -> list[dict[str, object]]:
    """Choose a reproducible sample while cycling across strata."""
    grouped: dict[tuple[str, bool, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        materialized = dict(row)
        grouped[
            (
                str(materialized["category"]),
                bool(materialized["has_eval"]),
                body_bucket(int(materialized["body_len"])),
            )
        ].append(materialized)

    if sum(len(items) for items in grouped.values()) < count:
        raise ValueError(f"Not enough rows to select {count} items")

    for key, items in grouped.items():
        grouped[key] = sorted(items, key=lambda item: _stable_score(item, seed=seed))

    chosen: list[dict[str, object]] = []
    strata = sorted(grouped)
    while len(chosen) < count:
        progressed = False
        for key in strata:
            if not grouped[key]:
                continue
            chosen.append(grouped[key].pop(0))
            progressed = True
            if len(chosen) == count:
                break
        if not progressed:
            break
    return chosen


def materialize_sample_entries(
    rows: Iterable[Mapping[str, object]],
) -> list[dict[str, str]]:
    """Reduce selected rows to the frozen fixture shape."""
    return [
        {
            "source": str(row["source"]),
            "project": str(row["name"]),
            "external_id": str(row["external_id"]),
        }
        for row in rows
    ]


async def load_candidate_rows(
    session: AsyncSession, *, states: list[str]
) -> list[dict[str, object]]:
    """Load candidate rows for a seeded sample using SELECT-only queries."""
    result = await session.execute(
        select(
            Project.category.label("category"),
            Project.name.label("name"),
            Issue.source.label("source"),
            Issue.external_id.label("external_id"),
            func.length(func.coalesce(Issue.body, "")).label("body_len"),
            LLMEvaluation.id.is_not(None).label("has_eval"),
        )
        .join(Project, Issue.project_id == Project.id)
        .outerjoin(
            LLMEvaluation,
            (LLMEvaluation.issue_id == Issue.id) & LLMEvaluation.latest.is_(True),
        )
        .where(Issue.state.in_(states))
    )
    return [dict(row) for row in result.mappings()]


async def run_select_sample(
    *,
    states: list[str],
    count: int,
    seed: int = DEFAULT_SEED,
) -> list[dict[str, str]]:
    """Load candidates from the configured DB and return a frozen sample."""
    settings = Settings()
    engine = get_engine(settings.database_url)
    try:
        session_factory = get_session_factory(engine)
        async with session_factory() as session:
            rows = await load_candidate_rows(session, states=states)
    finally:
        await engine.dispose()
    return materialize_sample_entries(
        choose_seeded_sample(rows, count=count, seed=seed)
    )


@click.command()
@click.option(
    "--state", "states", multiple=True, required=True, help="Issue states to include."
)
@click.option("--count", default=20, show_default=True, type=click.IntRange(min=1))
@click.option("--seed", default=DEFAULT_SEED, show_default=True, type=int)
@click.option("--out", "out_path", type=click.Path(path_type=Path), default=None)
def cli(states: tuple[str, ...], count: int, seed: int, out_path: Path | None) -> None:
    """Print or write a reproducible, stratified sample."""

    async def _main() -> list[dict[str, str]]:
        return await run_select_sample(states=list(states), count=count, seed=seed)

    sample = asyncio.run(_main())
    text = json.dumps(sample, indent=2) + "\n"
    if out_path is None:
        click.echo(text, nl=False)
    else:
        out_path.write_text(text)
        click.echo(f"Wrote {len(sample)} entries to {out_path}")


if __name__ == "__main__":
    cli()
