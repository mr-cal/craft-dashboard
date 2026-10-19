"""Worker version discovery."""

from __future__ import annotations

import importlib.metadata


def get_worker_version() -> str:
    """Return the installed version of craft-dashboard / craft-eval, or fallback."""
    for dist in ("craft-dashboard", "craft_dashboard", "craft-eval", "craft_eval"):
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
    try:
        from craft_dashboard._version import __version__
    except (ImportError, AttributeError):
        return "0.5.0"
    else:
        return __version__
