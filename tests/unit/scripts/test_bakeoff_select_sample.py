"""Unit tests for scripts.llm.bakeoff.select_sample."""

from __future__ import annotations

from click.testing import CliRunner
from scripts.llm.bakeoff.select_sample import (
    body_bucket,
    choose_seeded_sample,
    cli,
    materialize_sample_entries,
)


def _row(
    *,
    category: str,
    name: str,
    source: str,
    external_id: str,
    body_len: int,
    has_eval: bool,
) -> dict[str, object]:
    return {
        "category": category,
        "name": name,
        "source": source,
        "external_id": external_id,
        "body_len": body_len,
        "has_eval": has_eval,
    }


def test_body_bucket_splits_short_medium_long() -> None:
    assert body_bucket(10) == "short"
    assert body_bucket(800) == "medium"
    assert body_bucket(10_000) == "long"


def test_choose_seeded_sample_is_deterministic() -> None:
    rows = [
        _row(
            category="application",
            name=f"proj-{idx}",
            source="github",
            external_id=str(idx),
            body_len=idx * 100,
            has_eval=bool(idx % 2),
        )
        for idx in range(1, 25)
    ]
    first = choose_seeded_sample(rows, count=10, seed=41)
    second = choose_seeded_sample(rows, count=10, seed=41)
    assert first == second
    assert len(first) == 10


def test_materialize_sample_entries_keeps_only_frozen_fixture_fields() -> None:
    rows = [
        _row(
            category="library",
            name="craft-parts",
            source="github",
            external_id="123",
            body_len=50,
            has_eval=False,
        )
    ]
    assert materialize_sample_entries(rows) == [
        {"source": "github", "project": "craft-parts", "external_id": "123"}
    ]


def test_cli_rejects_non_positive_count() -> None:
    runner = CliRunner()

    result = runner.invoke(cli, ["--state", "open", "--count", "0"])

    assert result.exit_code != 0
    assert "--count" in result.output
