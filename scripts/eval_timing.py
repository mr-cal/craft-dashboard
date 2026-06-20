"""Persistent timing history for eval client ETA estimation.

Stores rolling windows of evaluation durations per phase in a JSON file at
``~/.craft-dashboard/eval-timing.json``.  The file is created on first use.
All I/O errors are silently ignored so the client degrades gracefully.
"""

from __future__ import annotations

import json
import pathlib
from datetime import timedelta

DEFAULT_PATH: pathlib.Path = (
    pathlib.Path.home() / ".craft-dashboard" / "eval-timing.json"
)
MAX_WINDOW: int = 100

# Phase key used throughout the eval client
PHASE_EVALUATE = "evaluate"


class TimingHistory:
    """Rolling window of per-phase evaluation durations for ETA estimation.

    Parameters
    ----------
    path:
        Override the default storage path (useful for testing).

    """

    def __init__(self, path: pathlib.Path | None = None) -> None:
        self.path = path or DEFAULT_PATH
        self._data: dict[str, list[float]] = self._load()

    # ------------------------------------------------------------------
    # Persistence

    def _load(self) -> dict[str, list[float]]:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text())
                if isinstance(raw, dict):
                    return {
                        k: [float(v) for v in vals]
                        for k, vals in raw.items()
                        if isinstance(k, str) and isinstance(vals, list)
                    }
        except Exception:
            return {}
        return {}

    def save(self) -> None:
        """Persist timing data to disk.  Errors are silently swallowed."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2))
        except Exception:  # noqa: S110
            pass

    # ------------------------------------------------------------------
    # Data management

    def add(self, phase: str, duration: float) -> None:
        """Record *duration* (seconds) for *phase* and persist."""
        window = self._data.setdefault(phase, [])
        window.append(round(duration, 2))
        if len(window) > MAX_WINDOW:
            self._data[phase] = window[-MAX_WINDOW:]
        self.save()

    def clear(self, phase: str) -> None:
        """Remove all samples for *phase* and persist."""
        self._data.pop(phase, None)
        self.save()

    # ------------------------------------------------------------------
    # Estimates

    def avg_seconds(self, phase: str) -> float | None:
        """Return mean duration in seconds, or ``None`` if no data."""
        window = self._data.get(phase, [])
        if not window:
            return None
        return sum(window) / len(window)

    def eta(self, phase: str, remaining: int) -> str:
        """Human-readable ETA string for *remaining* items.

        Returns ``"?"`` when no timing data is available and ``"—"`` when
        *remaining* is zero.
        """
        if remaining <= 0:
            return "\u2014"  # em dash
        avg = self.avg_seconds(phase)
        if avg is None:
            return "?"
        td = timedelta(seconds=int(avg * remaining))
        hours, remainder = divmod(td.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if td.days > 0:
            return f"{td.days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def sample_count(self, phase: str) -> int:
        """Return the number of samples currently stored for *phase*."""
        return len(self._data.get(phase, []))
