"""Tests for scripts.llm.checkpoint."""

import json
import shutil
from pathlib import Path

from scripts.llm.checkpoint import (
    EvaluationCheckpoint,
    clear_checkpoint,
    compute_filter_hash,
    load_checkpoint,
    save_checkpoint,
)


def _checkpoint_dir() -> Path:
    return Path.cwd() / ".test-llm-checkpoint"


def test_saves_and_loads_matching_checkpoint(monkeypatch) -> None:
    checkpoint_dir = _checkpoint_dir()
    checkpoint_file = checkpoint_dir / "llm-checkpoint.json"
    monkeypatch.setattr("scripts.llm.checkpoint.CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr("scripts.llm.checkpoint.CHECKPOINT_FILE", checkpoint_file)
    clear_checkpoint()

    checkpoint = EvaluationCheckpoint(
        filter_hash="filter-1",
        completed_issue_ids=[1, 2],
        timestamp="2025-01-01T00:00:00+00:00",
    )

    save_checkpoint(checkpoint)

    assert load_checkpoint("filter-1") == checkpoint
    clear_checkpoint()
    shutil.rmtree(checkpoint_dir, ignore_errors=True)


def test_load_returns_none_for_mismatched_filter(monkeypatch) -> None:
    checkpoint_dir = _checkpoint_dir()
    checkpoint_file = checkpoint_dir / "llm-checkpoint.json"
    monkeypatch.setattr("scripts.llm.checkpoint.CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr("scripts.llm.checkpoint.CHECKPOINT_FILE", checkpoint_file)
    clear_checkpoint()

    save_checkpoint(
        EvaluationCheckpoint(
            filter_hash="filter-1",
            completed_issue_ids=[1],
            timestamp="2025-01-01T00:00:00+00:00",
        )
    )

    assert load_checkpoint("filter-2") is None
    clear_checkpoint()
    shutil.rmtree(checkpoint_dir, ignore_errors=True)


def test_clear_checkpoint_removes_file(monkeypatch) -> None:
    checkpoint_dir = _checkpoint_dir()
    checkpoint_file = checkpoint_dir / "llm-checkpoint.json"
    monkeypatch.setattr("scripts.llm.checkpoint.CHECKPOINT_DIR", checkpoint_dir)
    monkeypatch.setattr("scripts.llm.checkpoint.CHECKPOINT_FILE", checkpoint_file)
    checkpoint_dir.mkdir(exist_ok=True)
    checkpoint_file.write_text(json.dumps({"filter_hash": "x"}))

    clear_checkpoint()

    assert not checkpoint_file.exists()
    shutil.rmtree(checkpoint_dir, ignore_errors=True)


def test_filter_hash_is_stable_for_same_inputs() -> None:
    first = compute_filter_hash(
        project_filter="snapcraft",
        limit=20,
        open_only=True,
        force=False,
        issue_filters=[("snapcraft", 1, 10)],
        incomplete=True,
        stale_days=7,
    )
    second = compute_filter_hash(
        project_filter="snapcraft",
        limit=20,
        open_only=True,
        force=False,
        issue_filters=[("snapcraft", 1, 10)],
        incomplete=True,
        stale_days=7,
    )

    assert first == second


def test_filter_hash_changes_when_filters_change() -> None:
    first = compute_filter_hash(project_filter="snapcraft", open_only=True)
    second = compute_filter_hash(project_filter="charmcraft", open_only=True)

    assert first != second
