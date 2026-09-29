#!/usr/bin/env python3
"""Compatibility entrypoint for the retention garbage collector.

``scripts/gc.py`` applies every retention policy, including transcripts. This
name is kept because mr-cal/vps-infra's cron.d entry refers to it. Prefer
``scripts/gc.py`` for anything new.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.gc import run_gc

if __name__ == "__main__":
    asyncio.run(run_gc())
