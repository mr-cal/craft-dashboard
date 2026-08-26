"""Bounded (project, sha) cache in front of reader.repo_layout.

A repo layout is identical for every issue evaluated at the same HEAD, so it
is computed once per commit-scanner pass and reused, not recomputed per
evaluation (design section 1). The cache is bounded with simple LRU eviction:
18 projects times a handful of live SHAs is small, but the bound guarantees it
can never grow without limit on the ~307MB VPS. Values are the compact
``dict[str, int]`` summaries (~40 entries each), so the ceiling is a few
hundred KB at most.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING

from craft_dashboard.git_mirrors.reader import repo_layout

if TYPE_CHECKING:
    import pathlib

#: Max distinct (project, sha) layouts retained before LRU eviction.
_MAX_CACHE_ENTRIES = 128

_cache: OrderedDict[tuple[str, str], dict[str, int]] = OrderedDict()
_lock = asyncio.Lock()


async def cached_repo_layout(
    project: str, mirror: pathlib.Path, *, ref: str
) -> dict[str, int]:
    """Return the depth-2 layout for (project, ref), computing it on a miss.

    Keyed by (project, ref) — NOT by mirror path — so the key matches the
    design's "(project, sha)" contract and stays stable regardless of where
    the mirror is mounted.
    """
    key = (project, ref)
    async with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)  # mark most-recently-used
            return cached

    # Compute outside the lock so concurrent misses on *different* keys do not
    # serialize behind one another's git subprocess.
    layout = await repo_layout(mirror, ref=ref)

    async with _lock:
        _cache[key] = layout
        _cache.move_to_end(key)
        while len(_cache) > _MAX_CACHE_ENTRIES:
            _cache.popitem(last=False)  # evict least-recently-used
    return layout


def clear_layout_cache() -> None:
    """Drop all cached layouts (used by tests and the scanner between passes)."""
    _cache.clear()


def cache_size() -> int:
    """Return the number of entries currently cached."""
    return len(_cache)
