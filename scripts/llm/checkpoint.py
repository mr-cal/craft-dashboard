"""Checkpoint helpers for resumable LLM evaluation runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.llm.queries import IssueFilter

CHECKPOINT_DIR = Path.home() / ".craft-dashboard"
CHECKPOINT_FILE = CHECKPOINT_DIR / "llm-checkpoint.json"


@dataclass
class EvaluationCheckpoint:
    """State for resuming an interrupted evaluation run."""

    filter_hash: str
    completed_issue_ids: list[int]
    timestamp: str


def save_checkpoint(checkpoint: EvaluationCheckpoint) -> None:
    """Persist the latest evaluation checkpoint to disk."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps(asdict(checkpoint), sort_keys=True))


def load_checkpoint(filter_hash: str) -> EvaluationCheckpoint | None:
    """Load a checkpoint when it matches the current filter set."""
    if not CHECKPOINT_FILE.exists():
        return None

    data = json.loads(CHECKPOINT_FILE.read_text())
    checkpoint = EvaluationCheckpoint(**data)
    if checkpoint.filter_hash != filter_hash:
        return None
    return checkpoint


def clear_checkpoint() -> None:
    """Remove any saved evaluation checkpoint."""
    CHECKPOINT_FILE.unlink(missing_ok=True)


def compute_filter_hash(
    *,
    project_filter: str = "",
    limit: int = 0,
    open_only: bool = False,
    force: bool = False,
    issue_filters: list[IssueFilter] | None = None,
    incomplete: bool = False,
    stale_days: int = 0,
) -> str:
    """Return a stable hash for the current evaluation selection parameters."""
    payload = {
        "project_filter": project_filter,
        "limit": limit,
        "open_only": open_only,
        "force": force,
        "issue_filters": sorted(issue_filters or []),
        "incomplete": incomplete,
        "stale_days": stale_days,
    }
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()
